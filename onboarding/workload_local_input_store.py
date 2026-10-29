# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Private mutable launch settings beside immutable workload identity history."""

from hashlib import sha256
import json
from .atomic_private_file import read_private_file
from .errors import OnboardingClientError
from .private_record_update import replace_private_record
from .process_lease import process_lease
from .proofs import canonical_json
from .workload_local_inputs import WorkloadLocalInputs


class WorkloadLocalInputStore:
    def __init__(self, draft_store):
        self.draft_store = draft_store

    def _path(self, draft):
        return self.draft_store.directory / f"{draft.proposal.id}-local.json"

    def load(self, draft):
        saved = self.draft_store.require_saved(draft)
        paired = self.draft_store.prepared_inputs(saved)
        if paired is not None:
            return paired
        try:
            record = json.loads(read_private_file(self._path(saved), limit=65536))
            if (not isinstance(record, dict) or set(record) != {"revision", "draft_sha256", "inputs"}
                or type(record["revision"]) is not int or record["revision"] != 1
                or record["draft_sha256"] != sha256(saved.encode()).hexdigest()):
                raise ValueError("exact local launch settings binding required")
            return WorkloadLocalInputs.from_payload(record["inputs"], proposal=saved.proposal)
        except FileNotFoundError as exc:
            raise OnboardingClientError("onboarding_workload_local_inputs_required") from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def save(self, *, draft, inputs):
        saved = self.draft_store.require_saved(draft)
        try:
            checked = WorkloadLocalInputs.from_payload(inputs.payload(), proposal=saved.proposal)
            record = canonical_json({"revision": 1, "draft_sha256": sha256(saved.encode()).hexdigest(),
                                     "inputs": checked.payload()})
            with process_lease(self.draft_store.directory / f".{saved.proposal.id}-local.lock") as held:
                if not held:
                    raise OnboardingClientError("onboarding_workload_local_inputs_busy")
                try:
                    self.load(saved)
                except OnboardingClientError as exc:
                    if exc.code != "onboarding_workload_local_inputs_required": raise
                if self.draft_store.prepared_inputs(saved) is not None:
                    record = self.draft_store.prepared_record(draft=saved, inputs=checked)
                    path = self.draft_store._path(saved.proposal.id)
                else:
                    path = self._path(saved)
                replace_private_record(path, record, limit=65536)
            return checked
        except (OSError, ValueError, TypeError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
