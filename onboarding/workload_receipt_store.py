# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Pin the first exact workload acknowledgement before further setup effects."""

from hashlib import sha256
import json

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .proofs import canonical_json
from .workload_request_receipt import WorkloadRequestReceipt


class WorkloadReceiptStore:
    def __init__(self, draft_store):
        self.draft_store = draft_store

    def _path(self, draft):
        return self.draft_store.directory / f"{draft.proposal.id}-receipt.json"

    def load(self, draft):
        saved = self.draft_store.require_saved(draft)
        try:
            record = json.loads(read_private_file(self._path(saved), limit=4096))
            if (not isinstance(record, dict) or set(record) != {"revision", "draft_sha256", "receipt"}
                    or type(record["revision"]) is not int or record["revision"] != 1
                    or record["draft_sha256"] != sha256(saved.encode()).hexdigest()):
                raise ValueError("exact immutable workload acknowledgement required")
            return WorkloadRequestReceipt.from_payload(record["receipt"], draft=saved)
        except FileNotFoundError as exc:
            raise OnboardingClientError("onboarding_workload_receipt_recovery_required") from exc
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def bind(self, *, draft, receipt):
        saved = self.draft_store.require_saved(draft)
        reviewed = WorkloadRequestReceipt.from_payload(receipt.payload(), draft=saved)
        try:
            create_private_file(self._path(saved), canonical_json({"revision": 1,
                "draft_sha256": sha256(saved.encode()).hexdigest(), "receipt": reviewed.payload()}))
        except FileExistsError:
            pass  # Mutable status is polled, not overwritten into initial history.
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        current = self.load(saved)
        if ((current.service_account_id, current.integration_id, current.intent_sha256) !=
                (reviewed.service_account_id, reviewed.integration_id, reviewed.intent_sha256)):
            raise OnboardingClientError("onboarding_workload_receipt_invalid")
        return current
