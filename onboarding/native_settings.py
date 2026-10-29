"""Reviewed native settings without a proposed key or execution authority."""

from dataclasses import dataclass


@dataclass(frozen=True)
class NativeSettings:
    display_name: str
    packaging: str
    native_host: str
    enforcement_mode: str
    coverage_posture: str

    @classmethod
    def from_payload(cls, payload):
        if not isinstance(payload, dict) or set(payload) != {
            "display_name", "profile", "enforcement_mode", "coverage_posture"}:
            raise ValueError("Closed native settings required")
        profile = payload["profile"]
        if (not isinstance(profile, dict) or set(profile) != {"account_type", "packaging", "native_host"}
                or profile["account_type"] != "agent" or profile["packaging"] not in ("sdk", "bridge")
                or profile["native_host"] not in ("codex", "claude-code")):
            raise ValueError("Reviewed native profile required")
        name = payload["display_name"]
        if (not isinstance(name, str) or not 1 <= len(name) <= 128 or name != name.strip()
                or any(ord(c) < 32 or ord(c) == 127 for c in name)):
            raise ValueError("Plain display name required")
        if (payload["enforcement_mode"] not in ("observe", "enforce")
                or payload["coverage_posture"] not in ("permissive", "strict")):
            raise ValueError("Closed native posture required")
        return cls(name, profile["packaging"], profile["native_host"],
            payload["enforcement_mode"], payload["coverage_posture"])

    def payload(self):
        return {"display_name": self.display_name,
            "profile": {"account_type": "agent", "packaging": self.packaging, "native_host": self.native_host},
            "enforcement_mode": self.enforcement_mode, "coverage_posture": self.coverage_posture}
