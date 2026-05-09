from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agentbridge.io import iter_files, load_json_or_yamlish, read_text
from agentbridge.models import Capability, SourceRef
from agentbridge.naming import capability_name, domain_from_resource, resource_from_path, singular, snake_case
from agentbridge.policy import classify_risk, confirmation_required, infer_action

HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
SOURCE_SUFFIXES = {".py", ".js", ".ts", ".tsx", ".jsx", ".java", ".kt", ".go", ".rb", ".php"}
SCHEMA_SUFFIXES = {".json", ".yaml", ".yml", ".graphql", ".gql", ".sql"}


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
        return discover_source_routes(file, read_text(file))


def discover_openapi(file: Path, spec: dict[str, Any]) -> list[Capability]:
    capabilities: list[Capability] = []
    paths = spec.get("paths", {})
    if not isinstance(paths, dict):
        return capabilities
    for route, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, operation in methods.items():
            if method.lower() not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId") or f"{method}_{route}"
            resource = resource_from_path(route)
            action = infer_action(method, operation_id, route)
            risk = classify_risk(action, method, route, operation_id)
            params = schema_from_openapi_operation(operation)
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
                    transport={"type": "http", "method": method.upper(), "path": route, "operation_id": operation_id},
                    dry_run_supported=method.upper() != "GET",
                )
            )
    return capabilities


def schema_from_openapi_operation(operation: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in operation.get("parameters", []) or []:
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
            match = re.match(r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s*:", line)
            if not match:
                continue
            field_name, args = match.group(1), match.group(2) or ""
            action = infer_action(None, field_name, "")
            if block_name == "Query" and action == "run":
                action = "list"
            resource = infer_resource_from_name(field_name, action)
            risk = classify_risk(action, None, "", field_name)
            if block_name == "Mutation" and risk == "read":
                risk = "write"
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
                    transport={"type": "graphql", "operation": block_name.lower(), "field": field_name},
                    dry_run_supported=block_name == "Mutation",
                )
            )
    return capabilities


def schema_from_graphql_args(args: str) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for name, typ, bang in re.findall(r"([A-Za-z_][A-Za-z0-9_]*)\s*:\s*([A-Za-z_\[\]!][A-Za-z0-9_\[\]!]*)\s*(!?)", args):
        clean_type = typ.replace("[", "").replace("]", "").replace("!", "")
        properties[snake_case(name)] = {"type": graphql_type_to_json_type(clean_type)}
        if "!" in typ or bang:
            required.append(snake_case(name))
    return object_schema(properties, required)


def discover_sql(file: Path, text: str) -> list[Capability]:
    capabilities: list[Capability] = []
    for match in re.finditer(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"]?([A-Za-z_][A-Za-z0-9_]*)[`\"]?\s*\((.*?)\);", text, re.I | re.S):
        table = singular(snake_case(match.group(1)))
        columns = parse_sql_columns(match.group(2))
        for action in ["list", "create", "update", "delete"]:
            risk = classify_risk(action)
            properties = columns if action in {"create", "update"} else {"id": {"type": "string"}}
            required = [] if action == "list" else ["id"] if action in {"update", "delete"} else []
            capabilities.append(
                Capability(
                    name=capability_name(action, table),
                    domain=domain_from_resource(table),
                    resource=table,
                    action=action,
                    description=f"{action} {table} records from database table {match.group(1)}",
                    input_schema=object_schema(properties, required),
                    risk=risk,
                    confirm_required=confirmation_required(risk),
                    source=SourceRef("database_schema", str(file), f"table {match.group(1)}"),
                    transport={"type": "database", "table": match.group(1)},
                    dry_run_supported=True,
                )
            )
    return capabilities


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
    seen: dict[str, int] = {}
    result: list[Capability] = []
    for cap in capabilities:
        base = cap.name
        count = seen.get(base, 0)
        seen[base] = count + 1
        if count:
            cap.name = f"{base}_{count + 1}"
        result.append(cap)
    return result

