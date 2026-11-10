"""Explicit MCP runtime selection binds approval, device and reviewed local inputs."""

from hashlib import sha256
import json
from pathlib import Path
from uuid import UUID, uuid4

from .atomic_private_file import read_private_file
from .contract_values import canonical_id
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .private_record_update import replace_private_record
from .process_lease import process_lease
from .proofs import canonical_json


def local_inputs_digest(inputs):
    return sha256(canonical_json(inputs.payload())).hexdigest()


class MCPRuntimeSelections:
    def __init__(self, directory):
        self.directory = Path(directory)
        create_private_directory(self.directory)
        self.path = self.directory / "selected.json"

    def load(self):
        try:
            raw = read_private_file(self.path, limit=32768)
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_mcp_selection_unavailable") from exc
        try:
            payload = json.loads(raw)
            if not isinstance(payload, list) or len(payload) > 32:
                raise ValueError("bounded MCP selection required")
            selected = {}
            for item in payload:
                if not isinstance(item, dict) or set(item) != {
                    "selection_id", "request_id", "device_id", "device_setup_id", "intent_sha256", "local_inputs_sha256"}:
                    raise ValueError("exact MCP selection fields required")
                for name in ("intent_sha256", "local_inputs_sha256"):
                    if (not isinstance(item[name], str) or len(item[name]) != 64
                            or any(c not in "0123456789abcdef" for c in item[name])):
                        raise ValueError("exact selection digest required")
                checked = {**item, **{name: canonical_id(item[name]) for name in
                            ("selection_id", "request_id", "device_id", "device_setup_id")}}
                if checked["request_id"] in selected:
                    raise ValueError("duplicate MCP selection")
                selected[checked["request_id"]] = checked
            return selected
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_mcp_selection_invalid") from exc

    def require_draft(self, selection, draft, inputs, receipt):
        if (draft.proposal.account_type != "mcp_server" or draft.proposal.device_id is None
                or receipt.device_id != draft.proposal.device_id
                or not isinstance(selection["selection_id"], UUID)
                or {name: value for name, value in selection.items() if name != "selection_id"}
                    != dict(request_id=draft.proposal.id, device_id=draft.proposal.device_id,
                    device_setup_id=draft.proposal.device_onboarding_session_id,
                    intent_sha256=receipt.intent_sha256, local_inputs_sha256=local_inputs_digest(inputs))):
            raise OnboardingClientError("onboarding_mcp_selection_changed")

    def select(self, *, draft, inputs, receipt, reviewed_local_inputs_sha256):
        selection = dict(selection_id=uuid4(), request_id=draft.proposal.id, device_id=draft.proposal.device_id,
            device_setup_id=draft.proposal.device_onboarding_session_id,
            intent_sha256=receipt.intent_sha256, local_inputs_sha256=local_inputs_digest(inputs))
        self.require_draft(selection, draft, inputs, receipt)
        if selection["local_inputs_sha256"] != reviewed_local_inputs_sha256:
            raise OnboardingClientError("onboarding_mcp_local_review_changed")
        with process_lease(self.directory / ".selection.lock") as held:
            if not held:
                raise OnboardingClientError("onboarding_mcp_selection_busy", retryable=True)
            selections = self.load()
            if len(selections) >= 32 and draft.proposal.id not in selections:
                raise OnboardingClientError("onboarding_mcp_selection_limit")
            selections[draft.proposal.id] = selection
            self._save(selections)

    def remove(self, request_id: UUID):
        if not isinstance(request_id, UUID):
            raise ValueError("typed MCP request ID required")
        with process_lease(self.directory / ".selection.lock") as held:
            if not held:
                raise OnboardingClientError("onboarding_mcp_selection_busy", retryable=True)
            selections = self.load()
            if selections.pop(request_id, None) is not None:
                self._save(selections)

    def _save(self, selections):
        values = [{name: str(value) for name, value in item.items()}
                  for _, item in sorted(selections.items())]
        replace_private_record(self.path, canonical_json(values), limit=32768)
