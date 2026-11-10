"""Bounded customer-owned selections; local target details are not uploaded."""

from dataclasses import dataclass
import json


@dataclass(frozen=True)
class SetupRequestChoices:
    native: tuple
    mcp: tuple

    @classmethod
    def from_payload(cls, value):
        if not isinstance(value, dict) or set(value) != {"native", "mcp"}:
            raise ValueError("Choose native and MCP connections explicitly")
        try:
            encoded = json.dumps(value, ensure_ascii=True).encode()
        except (TypeError, ValueError) as exc:
            raise ValueError("JSON setup selections required") from exc
        if len(encoded) > 7000:
            raise ValueError("Setup selections are too large")
        native, mcp = value["native"], value["mcp"]
        if not isinstance(native, list) or not isinstance(mcp, list) or len(native) + len(mcp) > 8:
            raise ValueError("Choose at most eight connections")
        hosts = set()
        for item in native:
            if (not isinstance(item, dict) or set(item) != {"host", "name"}
                    or not isinstance(item["host"], str)
                    or item["host"] not in {"codex", "claude-code"} or item["host"] in hosts):
                raise ValueError("Choose each supported native host once")
            hosts.add(item["host"])
            _name(item["name"])
        from .mcp_manual_target import manual_target
        from atellagent_client.discovery.mcp_connection import target_inputs
        for item in mcp:
            if isinstance(item, dict) and set(item) == {"name", "host", "discovered_name"}:
                _name(item["name"])
                _name(item["discovered_name"])
                from .setup_mcp_choice import choice_target
                target_inputs(choice_target(item))
                continue
            if not isinstance(item, dict) or set(item) != {"name", "url", "command", "arguments"}:
                raise ValueError("Choose a named local HTTP or stdio target")
            _name(item["name"])
            if (not isinstance(item["url"], str) or len(item["url"]) > 2048
                    or not isinstance(item["command"], str) or len(item["command"]) > 2048
                    or not isinstance(item["arguments"], list) or len(item["arguments"]) > 64
                    or any(not isinstance(argument, str) or len(argument) > 1024 for argument in item["arguments"])):
                raise ValueError("Bounded local target inputs required")
            if item["url"] and item["arguments"]:
                raise ValueError("HTTP targets cannot have executable arguments")
            from .setup_mcp_choice import choice_target
            target_inputs(choice_target(item))
        return cls(tuple(dict(item) for item in native), tuple(dict(item) for item in mcp))


def _name(value):
    if (not isinstance(value, str) or not value.strip() or len(value) > 128
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        raise ValueError("A bounded connection name is required")
