from __future__ import annotations

import importlib.util
import json
import sqlite3
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from agentbridge.runtime import validate_args

_PATH_PARAM_PATTERN_TEXT = r"\{([A-Za-z_][A-Za-z0-9_]*)\}|:([A-Za-z_][A-Za-z0-9_]*)"
_SENSITIVE_HEADER_NAMES = {"authorization", "cookie", "proxy-authorization", "x-api-key", "api-key"}


class AdapterError(ValueError):
    pass


@dataclass
class AdapterRuntimeConfig:
    base_url: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    timeout: float = 30.0
    graphql_endpoint: str = ""
    database_url: str = ""
    grpc_target: str = ""


class Adapter(Protocol):
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]: ...

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]: ...


class AdapterRegistry:
    def __init__(self) -> None:
        self._adapters: dict[str, Adapter] = {}

    def register(self, transport_type: str, adapter: Adapter) -> None:
        self._adapters[transport_type] = adapter

    def get(self, transport_type: str) -> Adapter:
        adapter = self._adapters.get(transport_type)
        if adapter is None:
            raise AdapterError(f"No runtime adapter registered for transport type: {transport_type or 'unknown'}")
        return adapter

    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        transport = capability.get("transport", {})
        return self.get(str(transport.get("type", ""))).preview(capability, args, config)

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        transport = capability.get("transport", {})
        return self.get(str(transport.get("type", ""))).execute(capability, args, config)


class HTTPAdapter:
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        return build_http_request_preview(capability, args, base_url=config.base_url, headers=config.headers)

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        return execute_http_tool(capability, args, config.base_url, headers=config.headers, timeout=config.timeout)


class GraphQLAdapter:
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        query, variables = build_graphql_query(capability, args)
        endpoint = config.graphql_endpoint or config.base_url
        return {
            "method": "POST",
            "url": endpoint,
            "headers": redact_headers({"Content-Type": "application/json", **config.headers}),
            "body": {"query": query, "variables": variables},
        }

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        endpoint = config.graphql_endpoint or config.base_url
        if not endpoint:
            raise AdapterError("--graphql-endpoint or --base-url is required when executing GraphQL tools")
        query, variables = build_graphql_query(capability, args)
        request_headers = {"Content-Type": "application/json", **config.headers}
        body = json.dumps({"query": query, "variables": variables}).encode("utf-8")
        req = urllib.request.Request(endpoint, data=body, headers=request_headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=config.timeout) as response:
                payload = response.read()
                return {
                    "tool": capability.get("name", ""),
                    "status": "executed",
                    "request": {"method": "POST", "url": endpoint, "body": {"query": query, "variables": variables}},
                    "response": format_http_response(response.status, dict(response.headers), payload),
                }
        except urllib.error.HTTPError as exc:
            payload = exc.read()
            return {
                "tool": capability.get("name", ""),
                "status": "http_error",
                "error": f"HTTP {exc.code}",
                "request": {"method": "POST", "url": endpoint, "body": {"query": query, "variables": variables}},
                "response": format_http_response(exc.code, dict(exc.headers), payload),
            }
        except urllib.error.URLError as exc:
            raise AdapterError(f"GraphQL request failed: {exc.reason}") from exc


class SQLReadOnlyAdapter:
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        query, params = build_select_query(capability, args)
        return {"dialect": "sqlite", "database_url": redact_database_url(config.database_url), "query": query, "params": params}

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        if not config.database_url:
            raise AdapterError("--database-url is required when executing SQL tools")
        query, params = build_select_query(capability, args)
        db_path = sqlite_path_from_url(config.database_url)
        try:
            with sqlite3.connect(db_path, timeout=config.timeout) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(query, params).fetchall()
        except sqlite3.Error as exc:
            raise AdapterError(f"SQL query failed: {exc}") from exc
        return {
            "tool": capability.get("name", ""),
            "status": "executed",
            "request": {"query": query, "params": params},
            "response": {"rows": [dict(row) for row in rows], "row_count": len(rows)},
        }


class GRPCAdapter:
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        transport = capability.get("transport", {})
        target = config.grpc_target or config.base_url
        service = transport.get("service", "")
        method = transport.get("method", "")
        return {
            "target": target,
            "method": f"{service}/{method}" if service and method else method,
            "message": args,
            "metadata": redact_headers(config.headers),
            "note": "gRPC execution uses grpcurl when available; dry-run previews never contact the target.",
        }

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        target = config.grpc_target or config.base_url
        if not target:
            raise AdapterError("--grpc-target or --base-url is required when executing gRPC tools")
        transport = capability.get("transport", {})
        service = str(transport.get("service", ""))
        method = str(transport.get("method", ""))
        if not service or not method:
            raise AdapterError("gRPC tool is missing service or method metadata")
        command = ["grpcurl", "-plaintext", "-d", json.dumps(args), target, f"{service}/{method}"]
        try:
            completed = subprocess.run(command, capture_output=True, text=True, timeout=config.timeout, check=False)
        except FileNotFoundError as exc:
            raise AdapterError("gRPC execution requires the grpcurl command on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdapterError(f"gRPC request timed out after {config.timeout:g} seconds") from exc
        payload = _parse_json_or_text(completed.stdout.strip())
        result = {
            "tool": capability.get("name", ""),
            "status": "executed" if completed.returncode == 0 else "grpc_error",
            "request": {"command": command[:2] + ["-d", "<json>", target, f"{service}/{method}"], "message": args},
            "response": payload,
        }
        if completed.returncode != 0:
            result["error"] = completed.stderr.strip() or f"grpcurl exited with {completed.returncode}"
        return result


class PythonPluginAdapter:
    def preview(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        plugin = load_plugin(capability)
        plan = getattr(plugin, "dry_run", None)
        if callable(plan):
            result = plan(capability, args, config)
            if isinstance(result, dict):
                return result
        return {"plugin": plugin.__name__, "args": args, "note": "Plugin has no dry_run() function; execution is still gated by AgentBridge policy."}

    def execute(self, capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
        plugin = load_plugin(capability)
        execute = getattr(plugin, "execute", None)
        if not callable(execute):
            raise AdapterError("Python plugin adapter requires an execute(capability, args, config) function")
        result = execute(capability, args, config)
        if not isinstance(result, dict):
            result = {"result": result}
        return {"tool": capability.get("name", ""), "status": "executed", "response": result}


DEFAULT_ADAPTER_REGISTRY = AdapterRegistry()
DEFAULT_ADAPTER_REGISTRY.register("http", HTTPAdapter())
DEFAULT_ADAPTER_REGISTRY.register("graphql", GraphQLAdapter())
DEFAULT_ADAPTER_REGISTRY.register("database", SQLReadOnlyAdapter())
DEFAULT_ADAPTER_REGISTRY.register("grpc", GRPCAdapter())
DEFAULT_ADAPTER_REGISTRY.register("python_plugin", PythonPluginAdapter())
DEFAULT_ADAPTER_REGISTRY.register("plugin", PythonPluginAdapter())


def build_request_preview(capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
    return DEFAULT_ADAPTER_REGISTRY.preview(capability, args, config)


def execute_tool(capability: dict[str, Any], args: dict[str, Any], config: AdapterRuntimeConfig) -> dict[str, Any]:
    return DEFAULT_ADAPTER_REGISTRY.execute(capability, args, config)


def execute_http_tool(
    capability: dict[str, Any],
    args: dict[str, Any],
    base_url: str,
    headers: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> dict[str, Any]:
    if not base_url:
        raise AdapterError("--base-url is required when --execute is enabled")

    validation = validate_args(capability.get("input_schema", {}), args)
    if validation["errors"]:
        raise AdapterError("; ".join(validation["errors"]))

    method, path = http_method_and_path(capability)
    url, remaining_args = build_http_url(base_url, path, args)
    data: bytes | None = None
    request_headers, query_args, body_args = split_http_args(capability, remaining_args, headers or {})
    if method not in {"GET", "HEAD", "OPTIONS"} and body_args:
        data = json.dumps(body_args).encode("utf-8")
        request_headers.setdefault("Content-Type", "application/json")
    elif method in {"GET", "HEAD", "OPTIONS"} and body_args:
        query_args = {**query_args, **body_args}
    if query_args:
        url = append_query(url, query_args)

    req = urllib.request.Request(url, data=data, headers=request_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            body = response.read()
            return {
                "tool": capability.get("name", ""),
                "status": "executed",
                "request": {"method": method, "url": url, "body": body_args if data else None},
                "response": format_http_response(response.status, dict(response.headers), body),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read()
        return {
            "tool": capability.get("name", ""),
            "status": "http_error",
            "error": f"HTTP {exc.code}",
            "request": {"method": method, "url": url, "body": body_args if data else None},
            "response": format_http_response(exc.code, dict(exc.headers), body),
        }
    except urllib.error.URLError as exc:
        raise AdapterError(f"HTTP request failed: {exc.reason}") from exc


def build_http_request_preview(
    capability: dict[str, Any],
    args: dict[str, Any],
    base_url: str = "",
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    method, path = http_method_and_path(capability)
    url, remaining_args = build_http_url(base_url, path, args)
    request_headers, query_args, body_args = split_http_args(capability, remaining_args, headers or {})
    body: dict[str, Any] | None = None
    if method in {"GET", "HEAD", "OPTIONS"}:
        query_args = {**query_args, **body_args}
    elif body_args:
        body = body_args
    if query_args:
        url = append_query(url, query_args)
    return {"method": method, "url": url, "headers": redact_headers(request_headers), "body": body}


def http_method_and_path(capability: dict[str, Any]) -> tuple[str, str]:
    transport = capability.get("transport", {})
    method = str(transport.get("method", "GET")).upper()
    path = str(transport.get("path", ""))
    if not path:
        raise AdapterError(f"HTTP tool {capability.get('name', '')} is missing transport.path")
    return method, path


def split_http_args(
    capability: dict[str, Any],
    args: dict[str, Any],
    headers: dict[str, str],
) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    transport = capability.get("transport", {})
    parameter_locations = transport.get("parameters", {}) if isinstance(transport.get("parameters"), dict) else {}
    query_keys = set(parameter_locations.get("query", []) or [])
    header_map = parameter_locations.get("header", {}) if isinstance(parameter_locations.get("header"), dict) else {}
    request_headers = dict(headers)
    query_args: dict[str, Any] = {}
    body_args: dict[str, Any] = {}
    for key, value in args.items():
        if key in query_keys:
            query_args[key] = value
        elif key in header_map:
            request_headers[str(header_map[key])] = str(value)
        else:
            body_args[key] = value
    return request_headers, query_args, body_args


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in headers.items():
        if key.lower() in _SENSITIVE_HEADER_NAMES or "token" in key.lower() or "secret" in key.lower():
            redacted[key] = "<redacted>"
        else:
            redacted[key] = value
    return redacted


def build_http_url(base_url: str, path: str, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    import re

    remaining = dict(args)

    def replace(match: re.Match[str]) -> str:
        key = match.group(1) or match.group(2)
        if key not in remaining:
            raise AdapterError(f"Missing path argument: {key}")
        value = remaining.pop(key)
        return urllib.parse.quote(str(value), safe="")

    rendered_path = re.compile(_PATH_PARAM_PATTERN_TEXT).sub(replace, path)
    return f"{base_url.rstrip('/')}/{rendered_path.lstrip('/')}", remaining


def append_query(url: str, query_args: dict[str, Any]) -> str:
    if not query_args:
        return url
    separator = "&" if urllib.parse.urlparse(url).query else "?"
    return f"{url}{separator}{urllib.parse.urlencode(query_args, doseq=True)}"


def format_http_response(status: int, headers: dict[str, str], body: bytes) -> dict[str, Any]:
    text = body.decode("utf-8", errors="replace")
    parsed: Any = None
    if text:
        parsed = _parse_json_or_text(text)
    return {"status": status, "headers": headers, "body": parsed}


def build_graphql_query(capability: dict[str, Any], args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    transport = capability.get("transport", {})
    operation = str(transport.get("operation", "query")).lower()
    field = str(transport.get("field", capability.get("name", "")))
    variables_meta = transport.get("variables", []) if isinstance(transport.get("variables", []), list) else []
    definitions: list[str] = []
    call_args: list[str] = []
    variables: dict[str, Any] = {}
    for item in variables_meta:
        if not isinstance(item, dict):
            continue
        gql_name = str(item.get("name", ""))
        arg_name = str(item.get("arg", gql_name))
        typ = str(item.get("type", "String"))
        if item.get("required") and not typ.endswith("!"):
            typ += "!"
        if not gql_name or arg_name not in args:
            continue
        definitions.append(f"${gql_name}: {typ}")
        call_args.append(f"{gql_name}: ${gql_name}")
        variables[gql_name] = args[arg_name]
    variable_definitions = f"({', '.join(definitions)})" if definitions else ""
    call = f"{field}({', '.join(call_args)})" if call_args else field
    selection = str(transport.get("selection", "") or "")
    return_type = str(transport.get("return_type", "") or "")
    if not selection and not _graphql_return_type_is_scalar(return_type):
        selection = " { __typename }"
    query = f"{operation} AgentBridge{field[:1].upper() + field[1:]}{variable_definitions} {{ {call}{selection} }}"
    return query, variables


def _graphql_return_type_is_scalar(return_type: str) -> bool:
    clean = return_type.replace("[", "").replace("]", "").replace("!", "").strip()
    return clean in {"ID", "String", "Int", "Float", "Boolean"}


def build_select_query(capability: dict[str, Any], args: dict[str, Any]) -> tuple[str, list[Any]]:
    transport = capability.get("transport", {})
    table = str(transport.get("table", ""))
    if not table:
        raise AdapterError("Database tool is missing transport.table")
    if str(transport.get("operation", capability.get("action", "list"))) not in {"list", "get", "select"}:
        raise AdapterError("SQL adapter is read-only and only executes SELECT tools")
    columns = transport.get("columns", [])
    if isinstance(columns, list) and columns:
        rendered_columns = ", ".join(quote_sql_identifier(str(col)) for col in columns)
    else:
        rendered_columns = "*"
    params: list[Any] = []
    where = ""
    if "id" in args and args["id"] not in (None, ""):
        where = " WHERE id = ?"
        params.append(args["id"])
    limit = args.get("limit", transport.get("default_limit", 100))
    try:
        limit_value = max(1, min(int(limit), int(transport.get("max_limit", 100))))
    except (TypeError, ValueError):
        limit_value = int(transport.get("default_limit", 100))
    query = f"SELECT {rendered_columns} FROM {quote_sql_identifier(table)}{where} LIMIT ?"
    params.append(limit_value)
    return query, params


def quote_sql_identifier(value: str) -> str:
    if not value.replace("_", "").isalnum() or not value:
        raise AdapterError(f"Unsafe SQL identifier: {value}")
    return '"' + value.replace('"', '""') + '"'


def sqlite_path_from_url(database_url: str) -> str:
    if database_url.startswith("sqlite:///"):
        return database_url[len("sqlite:///") - 1 :]
    if database_url.startswith("sqlite://"):
        return database_url[len("sqlite://") :]
    return database_url


def redact_database_url(database_url: str) -> str:
    if not database_url:
        return ""
    parsed = urllib.parse.urlparse(database_url)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}@", ":<redacted>@")
        return urllib.parse.urlunparse(parsed._replace(netloc=netloc))
    return database_url


def load_plugin(capability: dict[str, Any]) -> Any:
    transport = capability.get("transport", {})
    module_path = str(transport.get("module") or transport.get("path") or "")
    if not module_path:
        raise AdapterError("Python plugin tool is missing transport.module")
    path = Path(module_path)
    if not path.exists():
        raise AdapterError(f"Python plugin module not found: {module_path}")
    module_name = f"agentbridge_plugin_{abs(hash(str(path.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"Could not load Python plugin module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _parse_json_or_text(text: str) -> Any:
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text
