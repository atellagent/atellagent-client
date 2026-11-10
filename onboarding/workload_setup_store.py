# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Deployment-controlled private files, not a desktop secure-store requirement."""

from pathlib import Path
from uuid import UUID
import json
from cryptography.exceptions import UnsupportedAlgorithm

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .workload_setup_draft import WorkloadSetupDraft
from .workload_local_inputs import WorkloadLocalInputs
from .proofs import canonical_json


class WorkloadSetupStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _path(self, request_id):
        if not isinstance(request_id, UUID):
            raise ValueError("exact workload request identity required")
        return self.directory / f"{request_id}.json"

    def save_initial(self, draft: WorkloadSetupDraft):
        try:
            create_private_file(self._path(draft.proposal.id), draft.encode())
        except FileExistsError as exc:
            raise OnboardingClientError("onboarding_workload_setup_already_saved") from exc
        except (OSError, ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    @staticmethod
    def prepared_record(*, draft, inputs):
        if not isinstance(draft, WorkloadSetupDraft) or not isinstance(inputs, WorkloadLocalInputs):
            raise TypeError("typed workload draft and local inputs required")
        checked = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=draft.proposal)
        record = canonical_json({"revision": 2, "draft": json.loads(draft.encode()),
                                 "inputs": checked.payload()})
        if len(record) > 65536:
            raise ValueError("bounded prepared workload record required")
        return record

    def save_prepared(self, *, draft, inputs):
        """Commit initial identity and launch settings together, with no replacement."""
        try:
            record = self.prepared_record(draft=draft, inputs=inputs)
            create_private_file(self._path(draft.proposal.id), record)
        except FileExistsError as exc:
            raise OnboardingClientError("onboarding_workload_setup_already_saved") from exc
        except (OSError, ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _read_record(self, request_id):
        try:
            raw = read_private_file(self._path(request_id), limit=65536)
            payload = json.loads(raw)
            inputs = None
            if isinstance(payload, dict) and payload.get("revision") == 2:
                if (set(payload) != {"revision", "draft", "inputs"}
                        or type(payload["revision"]) is not int
                        or not isinstance(payload["inputs"], dict)):
                    raise ValueError("closed prepared workload record required")
                saved = WorkloadSetupDraft.decode(canonical_json(payload["draft"]))
                inputs = payload["inputs"]
            else:
                saved = WorkloadSetupDraft.decode(raw)
            if saved.proposal.id != request_id:
                raise ValueError("workload proposal identity changed")
            return saved, inputs
        except (OSError, ValueError, TypeError, KeyError, UnsupportedAlgorithm) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def load(self, request_id):
        return self._read_record(request_id)[0]

    def prepared_inputs(self, draft):
        """Return paired settings; legacy identities still use their separate record."""
        saved, payload = self._read_record(draft.proposal.id)
        if saved.encode() != draft.encode():
            raise OnboardingClientError("onboarding_workload_setup_intent_changed")
        if payload is None:
            return None
        try:
            return WorkloadLocalInputs.from_payload(payload, proposal=saved.proposal)
        except (ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def require_saved(self, draft):
        saved = self.load(draft.proposal.id)
        if saved.encode() != draft.encode():
            raise OnboardingClientError("onboarding_workload_setup_intent_changed")
        return saved
