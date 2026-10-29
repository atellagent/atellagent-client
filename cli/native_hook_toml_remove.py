"""Remove an exact generated socket permission without rewriting other TOML."""

from copy import deepcopy
import tomllib
import tomlkit

from .native_hook_toml_merge import merge, parse, _invalid


def remove(content, fragment):
    merge(content, fragment)  # Validate the exact fragment and host conflicts.
    document = parse(content)
    expected = deepcopy(document.unwrap())
    count = 0
    if "mcp_servers" in fragment and "atellagent" in document.get("mcp_servers", {}):
        del document["mcp_servers"]["atellagent"]
        del expected["mcp_servers"]["atellagent"]
        if not expected["mcp_servers"]:
            del document["mcp_servers"]
            del expected["mcp_servers"]
        count += 1
    if "network" in fragment:
        endpoint = fragment["network"]["allow_unix_sockets"][0]
        allowed = document.get("network", {}).get("allow_unix_sockets", [])
        if endpoint in allowed:
            expected["network"]["allow_unix_sockets"] = [item for item in allowed if item != endpoint]
            for index in range(len(allowed) - 1, -1, -1):
                if allowed[index] == endpoint:
                    del allowed[index]
                    count += 1
    if not count:
        return content, 0
    output = tomlkit.dumps(document).encode("utf-8")
    parse(output)
    if tomllib.loads(output.decode("utf-8")) != expected:
        _invalid()
    return output, count
