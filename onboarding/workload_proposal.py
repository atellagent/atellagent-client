# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Closed workload proposal wire facts; the server owns provisioning policy."""

from dataclasses import dataclass
from uuid import UUID

from .contract_values import canonical_id
from .setup_keys import _plain_text


@dataclass(frozen=True)
class WorkloadProposal:
    id: UUID
    display_name: str
    account_type: str
    packaging: str
    capabilities: tuple[str, ...]
    identity_mode: str | None = None
    control_source: str = "cluster_directive"
    local_guardrail_mode: str | None = None
    offline_dispatch_mode: str = "queue"
    offline_dispatch_deadline_seconds: int | None = 300
    channel_type: str | None = None
    filter_execution_boundary: str | None = None
    device_id: UUID | None = None
    device_onboarding_session_id: UUID | None = None

    def __post_init__(self):
        if not isinstance(self.id, UUID):
            raise ValueError("exact workload proposal identity required")
        _plain_text(self.display_name, 128)
        if (self.account_type not in ("agent", "workflow_runtime", "model", "ml_filter", "mcp_server", "channel")
                or self.packaging not in ("sdk", "bridge")):
            raise ValueError("supported connected workload profile required")
        if (not isinstance(self.capabilities, tuple) or not 1 <= len(self.capabilities) <= 128
                or len(set(self.capabilities)) != len(self.capabilities) or "agent.control" in self.capabilities):
            raise ValueError("unique workload capabilities required; native control is separate")
        for value in self.capabilities:
            _plain_text(value, 128)
        if (self.identity_mode not in (None, "boundary_identity_only", "federated_agent_identity")
                or self.control_source not in ("cluster_directive", "local_manifest")
                or self.local_guardrail_mode not in (None, "observe", "enforce")
                or self.offline_dispatch_mode not in ("queue", "fail_immediately")
                or self.filter_execution_boundary not in (None, "model_boundary", "tool_response", "egress")):
            raise ValueError("closed workload control facts required")
        deadline = self.offline_dispatch_deadline_seconds
        if deadline is not None and (type(deadline) is not int or not 30 <= deadline <= 604800):
            raise ValueError("bounded workload dispatch deadline required")
        if self.channel_type is not None:
            _plain_text(self.channel_type, 64)
        if ((self.device_id is None) != (self.device_onboarding_session_id is None)
                or any(value is not None and not isinstance(value, UUID)
                       for value in (self.device_id, self.device_onboarding_session_id))
                or self.device_id is not None and self.account_type != "mcp_server"):
            raise ValueError("Exact device-associated MCP scope required")

    def payload(self):
        values = {name: getattr(self, name) for name in self.__dataclass_fields__
                  if name not in ("account_type", "packaging")}
        values.update(id=str(self.id), capabilities=list(self.capabilities),
            profile={"account_type": self.account_type, "packaging": self.packaging, "native_host": None})
        for name in ("device_id", "device_onboarding_session_id"):
            values[name] = None if values[name] is None else str(values[name])
        return values

    @classmethod
    def from_payload(cls, payload):
        expected = (set(cls.__dataclass_fields__) - {"account_type", "packaging"}) | {"profile"}
        if not isinstance(payload, dict) or set(payload) != expected:
            raise ValueError("closed workload proposal required")
        profile = payload["profile"]
        if (not isinstance(profile, dict) or set(profile) != {"account_type", "packaging", "native_host"}
                or profile["native_host"] is not None or not isinstance(payload["capabilities"], list)):
            raise ValueError("workload profile cannot impersonate a native device")
        values = {name: value for name, value in payload.items() if name != "profile"}
        values.update(id=canonical_id(payload["id"]), capabilities=tuple(payload["capabilities"]),
                      account_type=profile["account_type"], packaging=profile["packaging"])
        for name in ("device_id", "device_onboarding_session_id"):
            values[name] = None if payload[name] is None else canonical_id(payload[name])
        return cls(**values)
