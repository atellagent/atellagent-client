"""Convert one explicitly selected local target; never connect or upload secrets."""

import os
from pathlib import Path
import shutil
import re

from atellagent_client.connected.mcp_client import _require_loopback_url
from atellagent_client.onboarding.workload_local_inputs import WorkloadLocalInputs


def target_inputs(target, *, credential_environment_names=None,
                  http_token_environment=None, http_auth_header=None):
    config = target.config
    mappings = dict(credential_environment_names or {})
    deployment = dict(type="bridge", target_transport=target.transport)
    if target.transport == "stdio":
        if http_token_environment is not None or http_auth_header is not None:
            raise ValueError("HTTP credential references do not apply to stdio targets")
        command = config.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ValueError("The discovered MCP server has no executable command")
        path = Path(command)
        if not path.is_absolute():
            if path.parent != Path(".") or "/" in command or "\\" in command:
                raise ValueError("Use an absolute executable path; relative project commands are not supported")
            resolved = shutil.which(command)
            if resolved is None:
                raise ValueError("The discovered MCP executable was not found in the current user environment")
            command = str(Path(resolved).absolute())
        args = config.get("args", [])
        environment = config.get("env", {})
        names = config.get("env_vars", [])
        if (not isinstance(environment, dict) or not isinstance(names, list)
                or any(not isinstance(name, str) for name in names)):
            raise ValueError("Unsupported discovered MCP environment settings")
        required = set(environment) | set(names)
        for name in required:
            if name not in mappings:
                if name in environment:
                    raise ValueError(f"Map {name} to a host environment variable; configuration credential values are not copied")
                mappings[name] = name
        if set(mappings) != required:
            raise ValueError("Credential mappings must exactly match the discovered target environment")
        if any(not isinstance(name, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name)
               for pair in mappings.items() for name in pair):
            raise ValueError("Credential mappings contain environment names, not values")
        if any(source not in os.environ for source in mappings.values()):
            raise ValueError("A mapped host environment variable is unavailable")
        deployment.update(target_command=command, target_args=args, target_env_map=mappings)
    elif target.transport == "http":
        deployment["target_url"] = _require_loopback_url(config.get("url", ""))
        if mappings:
            raise ValueError("Stdio environment mappings do not apply to HTTP targets")
        if http_auth_header is not None and http_token_environment is None:
            raise ValueError("An HTTP header requires a credential environment reference")
        declared_headers = config.get("headers") or config.get("http_headers") or {}
        if not isinstance(declared_headers, dict):
            raise ValueError("Unsupported discovered HTTP headers")
        if http_token_environment is not None:
            header = http_auth_header or "Authorization"
            if (not isinstance(http_token_environment, str)
                    or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", http_token_environment)
                    or not isinstance(header, str)
                    or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}", header)
                    or header.lower() in {"host", "content-length", "transfer-encoding", "connection"}):
                raise ValueError("Use a credential environment name and an ordinary HTTP authentication header")
            if http_token_environment not in os.environ:
                raise ValueError("The HTTP credential environment variable is unavailable")
            if any(not isinstance(key, str) or key.lower() != header.lower() for key in declared_headers):
                raise ValueError("Declared HTTP headers must match the selected credential header; values are never copied")
            deployment.update(upstream_auth_header=header, upstream_auth_token_env=http_token_environment)
        elif declared_headers or config.get("bearer_token_env_var"):
            raise ValueError("Choose a local HTTP credential environment reference; configuration credential values are not copied")
    else:
        raise ValueError("Connected MCP bridges support stdio or loopback streamable HTTP, not SSE/remote HTTP")
    return WorkloadLocalInputs(deployment=deployment, target_idempotent=True)
