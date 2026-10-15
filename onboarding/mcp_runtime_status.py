"""Local MCP process observations, not server authorization or readiness claims."""

from datetime import datetime, timezone
import json
from pathlib import Path

from .atomic_private_file import read_private_file
from .contract_values import aware_time, canonical_id
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .private_record_update import replace_private_record
from .proofs import canonical_json


class MCPRuntimeStatus:
    def __init__(self, directory):
        self.directory = Path(directory)
        create_private_directory(self.directory)

    def _path(self, request_id):
        canonical_id(str(request_id))
        return self.directory / f"{request_id}.json"

    def save(self, request_id, *, state, code=None, instance_id=None):
        value = dict(request_id=str(request_id), state=state, code=code, instance_id=instance_id,
                     updated_at=datetime.now(timezone.utc).isoformat())
        self._checked(value, request_id)
        replace_private_record(self._path(request_id), canonical_json(value), limit=2048)

    def load(self, request_id):
        try:
            value = json.loads(read_private_file(self._path(request_id), limit=2048))
            self._checked(value, request_id)
            updated = aware_time(value["updated_at"])
            if value["state"] in {"starting", "running"} and (datetime.now(timezone.utc) - updated).total_seconds() > 90:
                value["state"], value["code"] = "unavailable", "onboarding_mcp_status_stale"
            return value
        except FileNotFoundError:
            return dict(request_id=str(request_id), state="stopped", code=None, instance_id=None, updated_at=None)
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_mcp_status_unavailable") from exc

    @staticmethod
    def _checked(value, request_id):
        if (not isinstance(value, dict) or set(value) != {"request_id", "state", "code", "instance_id", "updated_at"}
                or value["request_id"] != str(request_id)
                or value["state"] not in {"starting", "running", "stopped", "failed"}
                or value["code"] is not None and (not isinstance(value["code"], str)
                    or len(value["code"]) > 128 or any(not (c.isalnum() or c == "_") for c in value["code"]))):
            raise ValueError("closed local MCP status required")
        if value["instance_id"] is not None:
            canonical_id(value["instance_id"])
        aware_time(value["updated_at"])
