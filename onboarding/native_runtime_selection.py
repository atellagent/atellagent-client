"""Explicit immutable host selection bound to the original admitted installation."""

import json
from pathlib import Path

from .atomic_private_file import create_private_file, read_private_file, sync_directory
from .contract_values import canonical_id
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .process_lease import process_lease

HOSTS = ("codex", "claude-code")


class NativeRuntimeSelections:
    def __init__(self, directory: Path):
        self.directory = directory
        create_private_directory(directory)

    def _path(self, host):
        if host not in HOSTS:
            raise OnboardingClientError("onboarding_native_host_invalid")
        return self.directory / f"{host}.json"

    def load(self, host):
        try:
            raw = read_private_file(self._path(host), limit=2048)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_native_selection_unavailable") from exc
        try:
            value = json.loads(raw)
            if (not isinstance(value, dict) or set(value) != {"host", "request_id", "setup_id", "device_id"}
                    or value["host"] != host):
                raise ValueError("closed host selection required")
            return {"host": host, **{name: canonical_id(value[name]) for name in
                ("request_id", "setup_id", "device_id")}}
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_native_selection_invalid") from exc

    def select(self, *, draft, binding):
        host = draft.proposal.native_host
        if (draft.proposal.id != binding.request_id or draft.device_id != binding.device.device_id
                or (draft.tenant_id, draft.owner_user_id) != (binding.device.tenant_id, binding.device.owner_user_id)):
            raise OnboardingClientError("onboarding_native_selection_invalid")
        value = dict(host=host, request_id=draft.proposal.id, setup_id=draft.setup_id, device_id=draft.device_id)
        raw = json.dumps({key: str(item) for key, item in value.items()}, sort_keys=True).encode()
        with process_lease(self.directory / ".selection.lock") as held:
            if not held:
                raise OnboardingClientError("onboarding_native_selection_busy", retryable=True)
            try:
                create_private_file(self._path(host), raw)
            except FileExistsError:
                if self.load(host) != value:
                    raise OnboardingClientError("onboarding_native_host_already_selected")
            except (OSError, ValueError) as exc:
                raise OnboardingClientError("onboarding_native_selection_unavailable") from exc

    def remove(self, *, draft):
        """Remove only the exact selected request; retain its proposal and keys."""
        with process_lease(self.directory / ".selection.lock") as held:
            if not held:
                raise OnboardingClientError("onboarding_native_selection_busy", retryable=True)
            selection = self.load(draft.proposal.native_host)
            if selection is None:
                return
            self.require_draft(selection, draft)
            self._path(selection["host"]).unlink()
            sync_directory(self.directory)

    def require_draft(self, selection, draft):
        if (selection["host"], selection["request_id"], selection["setup_id"], selection["device_id"]) != (
                draft.proposal.native_host, draft.proposal.id, draft.setup_id, draft.device_id):
            raise OnboardingClientError("onboarding_native_selection_invalid")
