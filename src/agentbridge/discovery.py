from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

from agentbridge.io import iter_files, load_json_or_yamlish, read_text
from agentbridge.models import Capability, SourceRef
from agentbridge.naming import capability_name, domain_from_resource, resource_from_path, singular, snake_case
from agentbridge.policy import classify_risk, confirmation_required, infer_action

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rb", ".php"}
SCHEMA_SUFFIXES = {".json", ".yaml", ".yml", ".graphql", ".gql", ".sql", ".proto"}


class CapabilityDiscoverer:
    def discover(self, paths: list[Path]) -> list[Capability]:
        capabilities: list[Capability] = []
        for file in iter_files(paths):
            suffix = file.suffix.lower()
            if suffix not in SOURCE_SUFFIXES | SCHEMA_SUFFIXES:
                continue
            try:
                capabilities.extend(self._discover_file(file))
            except Exception as exc:
                capabilities.append(
                    Capability(
                        name=f"inspect_{snake_case(file.stem)}",
                        domain="inspection",
                        resource=snake_case(file.stem),
                        action="inspect",
                        description=f"Discovery warning for {file}: {exc}",
                        input_schema=object_schema({}),
                        risk="read",
                        confirm_required=False,
                        source=SourceRef("warning", str(file), ""),
                        transport={"warning": str(exc)},
                    )
                )
        return dedupe_capabilities(capabilities)

    def _discover_file(self, file: Path) -> list[Capability]:
        suffix = file.suffix.lower()
        if suffix in {".json", ".yaml", ".yml"}:
            data = load_json_or_yamlish(file)
            if "openapi" in data or "swagger" in data or "paths" in data:
                return discover_openapi(file, data)
            return []
        if suffix in {".graphql", ".gql"}:
            return discover_graphql(file, read_text(file))
        if suffix == ".sql":
            return discover_sql(file, read_text(file))
        if suffix == ".proto":
            return discover_grpc(file, read_text(file))
        text = read_text(file)
        plugin_capabilities = discover_python_plugin(file, text) if suffix == ".py" else []
        return plugin_capabilities or discover_source_routes(file, text)


def discover_openapi(file: Path, spec: dict[str, Any]) -> list[Capability]:
    capabilities: list[Capability] = []
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return capabilities
    global_security = spec.get("security", [])
    security_schemes = ((spec.get("components") or {}).get("securitySchemes") or {})
    for route, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        path_parameters = methods.get("parameters", []) if isinstance(methods.get("parameters", []), list) else []
        for method, operation in methods.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId") or f"{method}_{route}"
            resource = resource_from_path(route)
            action = infer_action(method, operation_id, route)
            risk = classify_risk(action, method, route, operation_id)
            operation_parameters = list(path_parameters) + list(operation.get("parameters", []) or [])
            params = schema_from_openapi_operation(operation, operation_parameters)
            transport = {
                "type": "http",
                "method": method.upper(),
                "path": route,
                "operation_id": operation_id,
                "parameters": openapi_parameter_locations(operation_parameters),
            }
            auth = openapi_auth_requirements(operation, global_security, security_schemes)
            if auth:
                transport["auth"] = auth
            capabilities.append(
                Capability(
                    name=capability_name(action, resource),
                    domain=domain_from_resource(resource),
                    resource=resource,
                    action=action,
                    description=operation.get("summary") or operation.get("description") or f"{action} {resource}",
                    input_schema=params,
                    risk=risk,
                    confirm_required=confirmation_required(risk),
                    source=SourceRef("openapi", str(file), f"{method.upper()} {route}"),
                    transport=transport,
                    dry_run_supported=method.upper() != "GET",
                )
            )
    return capabilities


def schema_from_openapi_operation(operation: dict[str, Any], parameters: list[Any] | None = None) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in parameters if parameters is not None else operation.get("parameters", []) or []:
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not name:
            continue
        properties[snake_case(str(name))] = normalize_json_schema(param.get("schema", {"type": "string"}))
        if param.get("required"):
            required.append(snake_case(str(name)))
    body = (operation.get("requestBody") or {}).get("content", {})
    if isinstance(body, dict):
        for media in body.values():
            if isinstance(media, dict) and isinstance(media.get("schema"), dict):
                schema = normalize_json_schema(media["schema"])
                if schema.get("type") == "object":
                    properties.update(schema.get("properties", {}))
                    required.extend(schema.get("required", []))
                else:
                    properties["body"] = schema
                break
    return object_schema(properties, required)


def discover_graphql(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    for block_name, block_body in re.findall(r"type\s+(Query|Mutation)\s*\{([^}]*)\}", text, re.DOTALL):
        for line in block_body.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*:\s*([^#]+)", line)
            if not match:
                continue
            field_name, args, return_type = match.group(1), match.group(2) or "", match.group(3).strip()
            action = infer_action(None, field_name, "")
            if block_name == "Query" and action == "run":
                action = "list"
            resource = infer_resource_from_name(field_name, action)
            risk = classify_risk(action, None, "", field_name)
            if block_name == "Mutation" and risk == "read":
                risk = "write"
            variables = graphql_variables(args)
            capabilities.append(
                Capability(
                    name=capability_name(action, resource),
                    domain=domain_from_resource(resource),
                    resource=resource,
                    action=action,
                    description=f"GraphQL {block_name.lower()} field {field_name}",
                    input_schema=schema_from_graphql_args(args),
                    risk=risk,
                    confirm_required=confirmation_required(risk),
                    source=SourceRef("graphql", str(file), f"{block_name}.{field_name}"),
                    transport={
                        "type": "graphql",
                        "operation": block_name.lower(),
                        "field": field_name,
                        "variables": variables,
                        "return_type": return_type,
                    },
                    dry_run_supported=block_name == "Mutation",
                )
            )
    return capabilities


def schema_from_graphql_args(args: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for item in graphql_variables(args):
        properties[item["arg"]] = {"type": graphql_type_to_json_type(str(item["type"]).replace("!", ""))}
        if item["required"]:
            required.append(item["arg"])
    return object_schema(properties, required)


def graphql_variables(args: str) -> list[dict[str, Any]]:
    variables: list[dict[str, Any]] = []
    for name, typ in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_\[\]!][A-Za-z0-9_\[\]!]*)", args):
        clean_type = typ.replace("[", "").replace("]", "")
        variables.append(
            {
                "name": name,
                "arg": snake_case(name),
                "type": clean_type,
                "required": clean_type.endswith("!"),
            }
        )
    return variables


def discover_sql(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    for match in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*\((.*?)\);", text, re.I | re.S):
        table_name = match.group(1)
        table = singular(snake_case(table_name))
        columns = parse_sql_columns(match.group(2))
        properties = {
            "id": {"type": "string", "description": "Optional primary-key filter."},
            "limit": {"type": "number", "description": "Maximum rows to return. Defaults to 100 and is capped at 100."},
        }
        capabilities.append(
            Capability(
                name=capability_name("list", table),
                domain=domain_from_resource(table),
                resource=table,
                action="list",
                description=f"List {table} records from database table {table_name} with a read-only SELECT.",
                input_schema=object_schema(properties, []),
                risk="read",
                confirm_required=False,
                source=SourceRef("database_schema", str(file), f"table {table_name}"),
                transport={
                    "type": "database",
                    "operation": "list",
                    "table": table_name,
                    "columns": list(columns),
                    "read_only": True,
                    "default_limit": 100,
                    "max_limit": 100,
                },
                dry_run_supported=True,
            )
        )
    return capabilities


def discover_grpc(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    messages = parse_proto_messages(text)
    for service_match in re.finditer(r"service\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}", text, re.S):
        service_name, body = service_match.group(1), service_match.group(2)
        for rpc_name, request_type, response_type in re.findall(
            r"rpc\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)\s*returns\s*\(\s*([A-Za-z_][A-Za-z0-9_.]*)\s*\)",
            body,
        ):
            action = infer_action(None, rpc_name, "")
            resource = infer_resource_from_name(rpc_name, action)
            risk = classify_risk(action, None, "", rpc_name)
            capabilities.append(
                Capability(
                    name=capability_name(action, resource),
                    domain=domain_from_resource(resource),
                    resource=resource,
                    action=action,
                    description=f"gRPC {service_name}/{rpc_name} method from proto service {service_name}",
                    input_schema=object_schema(messages.get(request_type.split(".")[-1], {}), []),
                    risk=risk,
                    confirm_required=confirmation_required(risk),
                    source=SourceRef("grpc_proto", str(file), f"{service_name}/{rpc_name}"),
                    transport={
                        "type": "grpc",
                        "service": service_name,
                        "method": rpc_name,
                        "request_type": request_type,
                        "response_type": response_type,
                    },
                    dry_run_supported=True,
                )
            )
    return capabilities


def discover_python_plugin(file: Path, text: str) -> list[Capability]:
    if "agentbridge_discover" not in text and "AGENTBRIDGE_PLUGIN" not in text:
        return []
    module_name = f"agentbridge_discovery_plugin_{abs(hash(str(file.resolve())))}"
    spec = importlib.util.spec_from_file_location(module_name, file)
    if spec is None or spec.loader is None:
        return []
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not bool(getattr(module, "AGENTBRIDGE_PLUGIN", False)) and not hasattr(module, "agentbridge_discover"):
        return []
    discover = getattr(module, "agentbridge_discover", None) or getattr(module, "discover", None)
    if not callable(discover):
        return []
    raw = discover()
    if not isinstance(raw, list):
        return []
    capabilities: list[Capability] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        data = dict(item)
        source = dict(data.get("source", {}) or {})
        source.setdefault("kind", "custom_python_plugin")
        source["path"] = str(file)
        source.setdefault("location", getattr(discover, "__name__", "agentbridge_discover"))
        data["source"] = source
        transport = dict(data.get("transport", {}) or {})
        transport.setdefault("type", "python_plugin")
        transport.setdefault("module", str(file))
        data["transport"] = transport
        capabilities.append(Capability.from_dict(data))
    return capabilities


def parse_proto_messages(text: str) -> dict[str, dict[str, Any]]:
    messages: dict[str, dict[str, Any]] = {}
    for message_name, body in re.findall(r"message\s+([A-Za-z_][A-Za-z0-9_]*)\s*\{(.*?)\}", text, re.S):
        fields: dict[str, Any] = {}
        for typ, name in re.findall(r"(?:optional|required|repeated)?\s*([A-Za-z_][A-Za-z0-9_.<>]*)\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*\d+", body):
            fields[snake_case(name)] = {"type": proto_type_to_json_type(typ)}
        messages[message_name] = fields
    return messages


def openapi_parameter_locations(parameters: list[Any]) -> dict[str, Any]:
    result: dict[str, Any] = {"path": [], "query": [], "header": {}, "cookie": []}
    for param in parameters:
        if not isinstance(param, dict) or not param.get("name"):
            continue
        key = snake_case(str(param["name"]))
        location = str(param.get("in", "query"))
        if location == "header":
            result["header"][key] = str(param["name"])
        elif location in result and isinstance(result[location], list):
            result[location].append(key)
    return {key: value for key, value in result.items() if value}


def openapi_auth_requirements(
    operation: dict[str, Any],
    global_security: Any,
    security_schemes: Any,
) -> list[dict[str, Any]]:
    if not isinstance(security_schemes, dict):
        return []
    requirements = operation.get("security", global_security)
    if not isinstance(requirements, list):
        return []
    result: list[dict[str, Any]] = []
    for requirement in requirements:
        if not isinstance(requirement, dict):
            continue
        for scheme_name, scopes in requirement.items():
            scheme = security_schemes.get(scheme_name, {})
            if not isinstance(scheme, dict):
                continue
            item = {
                "scheme": scheme_name,
                "type": scheme.get("type", ""),
                "scopes": scopes if isinstance(scopes, list) else [],
            }
            if scheme.get("type") == "http":
                item["scheme_type"] = scheme.get("scheme", "")
                item["bearer_format"] = scheme.get("bearerFormat", "")
            if scheme.get("type") == "apiKey":
                item["in"] = scheme.get("in", "")
                item["name"] = scheme.get("name", "")
                if scheme.get("in") == "header" and scheme.get("name"):
                    item["runtime_header"] = scheme.get("name")
                if scheme.get("in") == "query" and scheme.get("name"):
                    item["runtime_query"] = scheme.get("name")
            if scheme.get("type") in {"oauth2", "openIdConnect"}:
                item["flows"] = sorted((scheme.get("flows") or {}).keys()) if isinstance(scheme.get("flows"), dict) else []
                item["open_id_connect_url"] = scheme.get("openIdConnectUrl", "")
            result.append(item)
    return result


def proto_type_to_json_type(value: str) -> str:
    if value in {"double", "float", "int32", "int64", "uint32", "uint64", "sint32", "sint64", "fixed32", "fixed64", "sfixed32", "sfixed64"}:
        return "number"
    if value == "bool":
        return "boolean"
    return "string"


def discover_source_routes(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    capabilities.extend(discover_python_routes(file, text))
    capabilities.extend(discover_js_routes(file, text))
    capabilities.extend(discover_java_routes(file, text))
    return capabilities


def discover_python_routes(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    pattern = re.compile(r"@(?:app|router|blueprint)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']+)[\"'][^)]*\)\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.S)
    for method, route, func, args in pattern.findall(text):
        capabilities.append(capability_from_route(file, "source_route", method.upper(), route, func, args))
    flask = re.compile(r"@(?:app|blueprint)\.route\(\s*[\"']([^\"']+)[\"'][^)]*methods\s*=\s*\[([^\]]+)\][^)]*\)\s*(?:async\s+)?def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.S)
    for route, methods, func, args in flask.findall(text):
        for method in re.findall(r"[\"']([A-Z]+)[\"']", methods):
            capabilities.append(capability_from_route(file, "source_route", method, route, func, args))
    return capabilities


def discover_js_routes(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    pattern = re.compile(r"(?:app|router)\.(get|post|put|patch|delete)\(\s*[`\"']([^`\"']+)[`\"']\s*,\s*(?:async\s*)?(?:function\s+)?([A-Za-z_][A-Za-z0-9_]*)?", re.S)
    for method, route, func in pattern.findall(text):
        capabilities.append(capability_from_route(file, "source_route", method.upper(), route, func or f"{method}_{resource_from_path(route)}", "req, res"))
    return capabilities


def discover_java_routes(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    class_prefix = ""
    class_match = re.search(r"@RequestMapping\(\s*[\"']([^\"']+)[\"']\s*\)\s*(?:public\s+)?class", text)
    if class_match:
        class_prefix = class_match.group(1)
    pattern = re.compile(r"@(GetMapping|PostMapping|PutMapping|PatchMapping|DeleteMapping|RequestMapping)\(([^)]*)\)\s*(?:public|private|protected)?\s+[A-Za-z0-9_<>, ?]+\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(([^)]*)\)", re.S)
    method_map = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
    }
    for annotation, body, func, args in pattern.findall(text):
        method = method_map.get(annotation, "GET")
        if annotation == "RequestMapping":
            method_match = re.search(r"method\s*=\s*RequestMethod\.([A-Z]+)", body)
            if method_match:
                method = method_match.group(1)
        route_match = re.search(r"[\"']([^\"']*)[\"']", body)
        route = (class_prefix + "/" + route_match.group(1).lstrip("/")) if route_match else class_prefix or f"/{func}"
        capabilities.append(capability_from_route(file, "source_route", method, route, func, args))
    return capabilities


def capability_from_route(file: Path, kind: str, method: str, route: str, function_name: str, args: str) -> Capability:
    action = infer_action(method, function_name, route)
    resource = resource_from_path(route) if resource_from_path(route) != "resource" else infer_resource_from_name(function_name, action)
    risk = classify_risk(action, method, route, function_name)
    return Capability(
        name=capability_name(action, resource),
        domain=domain_from_resource(resource),
        resource=resource,
        action=action,
        description=f"{method} {route} handled by {function_name}",
        input_schema=schema_from_function_args(args, route),
        risk=risk,
        confirm_required=confirmation_required(risk),
        source=SourceRef(kind, str(file), f"{method} {route}"),
        transport={"type": "http", "method": method, "path": route, "handler": function_name},
        dry_run_supported=method != "GET",
    )


def schema_from_function_args(args: str, route: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in re.findall(r"[{:]([A-Za-z_][A-Za-z0-9_]*)", route):
        key = snake_case(param)
        properties[key] = {"type": "string"}
        required.append(key)
    for arg in re.split(r",", args):
        name = snake_case(arg.split(":")[0].strip().split(" ")[-1])
        if not name or name in {"self", "request", "req", "res", "response"}:
            continue
        properties.setdefault(name, {"type": "string"})
    return object_schema(properties, required)


def parse_sql_columns(body: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    for raw in body.splitlines():
        line = raw.strip().rstrip(",")
        if not line or line.upper().startswith(("PRIMARY ", "FOREIGN ", "UNIQUE ", "KEY ", "CONSTRAINT ", "INDEX ")):
            continue
        match = re.match(r"[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?\s+([A-Za-z0-9_()]+)", line)
        if not match:
            continue
        name, typ = match.group(1), match.group(2).upper()
        properties[snake_case(name)] = {"type": sql_type_to_json_type(typ)}
    return properties


def infer_resource_from_name(name: str, action: str) -> str:
    cleaned = snake_case(name)
    for prefix in [action, "get", "list", "create", "update", "delete", "remove", "send", "publish", "rewrite"]:
        if cleaned.startswith(prefix + "_"):
            cleaned = cleaned[len(prefix) + 1 :]
    parts = [p for p in cleaned.split("_") if p not in {"by", "id", "all"}]
    return singular(parts[-1] if parts else cleaned or "resource")


def normalize_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    if "$ref" in schema:
        return {"type": "object", "description": schema["$ref"]}
    result = dict(schema)
    if "type" not in result:
        result["type"] = "string"
    if result.get("type") == "object":
        result["properties"] = {snake_case(k): normalize_json_schema(v) for k, v in result.get("properties", {}).items()}
    return result


def graphql_type_to_json_type(value: str) -> str:
    if value in {"Int", "Float"}:
        return "number"
    if value == "Boolean":
        return "boolean"
    return "string"


def sql_type_to_json_type(value: str) -> str:
    if any(token in value for token in ["INT", "DECIMAL", "FLOAT", "DOUBLE", "NUMERIC", "REAL"]):
        return "number"
    if any(token in value for token in ["BOOL"]):
        return "boolean"
    return "string"


def object_schema(properties: dict[str, Any], required: list[str] | None = None) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(set(required or [])),
        "additionalProperties": False,
    }


def dedupe_capabilities(capabilities: list[Capability]) -> list[Capability]:
    by_identity: dict[tuple[str, ...], Capability] = {}
    identity_order: list[tuple[str, ...]] = []
    for cap in capabilities:
        identity = capability_identity(cap)
        existing = by_identity.get(identity)
        if existing is None:
            by_identity[identity] = cap
            identity_order.append(identity)
        elif source_priority(cap.source.kind) > source_priority(existing.source.kind):
            by_identity[identity] = cap

    unique = [by_identity[identity] for identity in identity_order]
    used_names: set[str] = set()
    result: list[Capability] = []
    for cap in unique:
        base = cap.name
        if base in used_names:
            cap.name = disambiguated_capability_name(cap, base, used_names)
        used_names.add(cap.name)
        result.append(cap)
    return result


def capability_identity(capability: Capability) -> tuple[str, ...]:
    transport = capability.transport
    transport_type = str(transport.get("type", ""))
    if transport_type == "http":
        return (
            "http",
            str(transport.get("method", "GET")).upper(),
            normalize_transport_path(str(transport.get("path", ""))),
        )
    if transport_type == "graphql":
        return (
            "graphql",
            str(transport.get("operation", "")),
            str(transport.get("field", "")),
        )
    if transport_type == "database":
        return (
            "database",
            str(transport.get("table", "")),
            capability.action,
        )
    if transport_type == "inferred":
        return (
            "inferred",
            capability.action,
            capability.resource,
            ",".join(sorted(capability.input_schema.get("required", []))),
        )
    return (
        transport_type or capability.source.kind,
        capability.action,
        capability.resource,
        capability.source.location,
    )


def normalize_transport_path(path: str) -> str:
    normalized = re.sub(r":([A-Za-z_][A-Za-z0-9_]*)", r"{\1}", path)
    normalized = re.sub(r"\{[A-Za-z_][A-Za-z0-9_]*\}", "{}", normalized)
    return normalized.rstrip("/") or "/"


def source_priority(kind: str) -> int:
    return {
        "openapi": 50,
        "graphql": 45,
        "source_route": 40,
        "database_schema": 30,
        "ai_inferred": 20,
        "warning": 0,
    }.get(kind, 10)


def disambiguated_capability_name(
    capability: Capability,
    base: str,
    used_names: set[str],
) -> str:
    transport = capability.transport
    hints = [
        str(transport.get("operation_id", "")),
        str(transport.get("handler", "")),
        str(transport.get("field", "")),
    ]
    path = str(transport.get("path", ""))
    literal_path_parts = [
        snake_case(part)
        for part in path.split("/")
        if part and not part.startswith(("{", ":"))
    ]
    hints.extend(reversed(literal_path_parts))
    if transport.get("type") == "database":
        hints.append(f"database_{transport.get('table', capability.resource)}")
    hints.extend(capability.input_schema.get("required", []))

    base_parts = set(base.split("_"))
    for hint in hints:
        if not str(hint).strip():
            continue
        hint_parts = [part for part in snake_case(hint).split("_") if part not in base_parts]
        if not hint_parts:
            continue
        candidate = snake_case(f"{base}_{'_'.join(hint_parts)}")
        if candidate not in used_names:
            return candidate

    source_hint = snake_case(capability.source.location)
    candidate = snake_case(f"{base}_{source_hint}")
    if candidate not in used_names:
        return candidate
    raise ValueError(f"Could not generate a unique semantic name for capability {base}")
