"""Explicit local MCP targets, independent of host configuration or discovery."""

from atellagent_client.discovery.targets import ProbeTarget


def manual_target(*, url=None, command=None, arguments=(), credential_environment_names=None):
    if bool(url) == bool(command):
        raise ValueError("Choose one local MCP URL or executable")
    if url:
        if arguments or credential_environment_names:
            raise ValueError("Executable arguments and mappings do not apply to HTTP targets")
        return ProbeTarget("manual", "http", {"url": url})
    return ProbeTarget("manual", "stdio", {"command": command, "args": list(arguments),
        "env_vars": list(credential_environment_names or {})})
