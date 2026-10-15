# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Customer launch inputs, never approved identity or remotely delivered commands."""

from dataclasses import dataclass, field
from pathlib import Path
import re
from atellagent_client.sdk.config_deployment import _parse_deployment_config, deployment_config_to_dict

_HANDLER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*:[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


@dataclass(frozen=True)
class WorkloadLocalInputs:
    deployment: dict = field(repr=False)
    local_guardrail_manifest_path: str | None = None
    handler: str | None = None
    mcp_manifest_path: str | None = None
    target_idempotent: bool = False

    def payload(self):
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @classmethod
    def from_payload(cls, payload, *, proposal):
        if not isinstance(payload, dict) or set(payload) != set(cls.__dataclass_fields__):
            raise ValueError("closed local workload launch inputs required")
        values = dict(payload)
        values["deployment"] = deployment_config_to_dict(
            _parse_deployment_config(values["deployment"], packaging=proposal.packaging))
        if type(values["target_idempotent"]) is not bool:
            raise ValueError("explicit local idempotency assertion required")
        handler = values["handler"]
        if handler is not None and (not isinstance(handler, str) or not _HANDLER.fullmatch(handler)):
            raise ValueError("local handler must use module:attribute notation")
        for name in ("local_guardrail_manifest_path", "mcp_manifest_path"):
            path = values[name]
            if path is not None and (not isinstance(path, str) or path != path.strip()
                or not path or not Path(path).is_absolute()):
                raise ValueError("explicit absolute local manifest path required")
        if proposal.control_source == "local_manifest":
            if values["local_guardrail_manifest_path"] is None:
                raise ValueError("local guardrail manifest required by approved profile")
        elif values["local_guardrail_manifest_path"] is not None:
            raise ValueError("guardrail manifest requires approved local-manifest control")
        if proposal.account_type != "mcp_server" and values["mcp_manifest_path"] is not None:
            raise ValueError("MCP manifest requires an approved MCP workload")
        return cls(**values)
