from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, data: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(data, encoding="utf-8")


def load_json_or_yamlish(path: Path) -> dict[str, Any]:
    text = read_text(path)
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(text)
        return loaded if isinstance(loaded, dict) else {}
    except Exception:
        return parse_minimal_yaml(text)


def parse_minimal_yaml(text: str) -> dict[str, Any]:
    """Tiny YAML fallback for simple OpenAPI files.

    It intentionally supports only the subset commonly found in generated specs:
    nested mappings, scalar strings, booleans, and empty objects. If parsing gets
    ambiguous, callers still keep working because discovery is best-effort.
    """

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        match = re.match(r"([^:]+):(.*)$", line)
        if not match:
            continue
        key = match.group(1).strip().strip("\"'")
        value = match.group(2).strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        elif value in {"{}", "[]"}:
            parent[key] = {} if value == "{}" else []
        elif value.lower() in {"true", "false"}:
            parent[key] = value.lower() == "true"
        else:
            parent[key] = value.strip("\"'")
    return root


def iter_files(paths: list[Path]) -> list[Path]:
    result: list[Path] = []
    ignored = {".git", ".venv", "venv", "node_modules", "__pycache__", ".agentbridge", "build", "dist"}
    for path in paths:
        if path.is_file():
            result.append(path)
            continue
        if not path.exists():
            continue
        for child in path.rglob("*"):
            if any(part in ignored for part in child.parts):
                continue
            if child.is_file():
                result.append(child)
    return sorted(result)

