"""Local MCP tool presentation; no identity or credential configuration."""

from pathlib import Path
import yaml

from .contracts import MCPProxyTool


def load_agent_tools(path):
    with Path(path).open("rb") as source:
        encoded = source.read(1024 * 1024 + 1)
    if len(encoded) > 1024 * 1024:
        raise ValueError("tool catalog is too large")
    try:
        document = yaml.safe_load(encoded.decode("utf-8"))
    except (UnicodeDecodeError, yaml.YAMLError) as exc:
        raise ValueError("tool catalog must be valid UTF-8 YAML") from exc
    if not isinstance(document, dict) or set(document) != {"tools"}:
        raise ValueError("tool catalog must contain only tools, not identity settings")
    values = document["tools"]
    if not isinstance(values, list) or not 1 <= len(values) <= 1024:
        raise ValueError("tools must be a bounded non-empty list")
    fields = {"name", "description", "input_schema", "target_binding", "target_tool_name"}
    tools, names = [], set()
    for value in values:
        if not isinstance(value, dict) or set(value) != fields:
            raise ValueError("tool requires name, description, input_schema, target_binding and target_tool_name")
        if not all(isinstance(value[name], str) for name in fields - {"input_schema"}):
            raise ValueError("tool labels and bindings must be text")
        if not isinstance(value["input_schema"], dict):
            raise ValueError("input_schema must be an object")
        tool = MCPProxyTool(**value)
        if tool.name in names:
            raise ValueError("tool names must be unique")
        names.add(tool.name)
        tools.append(tool)
    return tuple(tools)
