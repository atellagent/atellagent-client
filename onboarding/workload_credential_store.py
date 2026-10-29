# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Commit one exact initial workload credential before mTLS activation."""

import json
from pathlib import Path
import re
from uuid import UUID

from cryptography.hazmat.primitives import serialization
from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .credential_generation import commit_credential_generation
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .service_account_delivery_payload import service_account_delivery_payload
from .workload_credential_record import (StoredWorkloadCredential, workload_key_pem,
    verify_workload_credential, workload_credential_metadata)
from .workload_request_receipt import WorkloadRequestReceipt
from .renewed_credential import retained_history_time


class WorkloadCredentialStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _pointer(self, request_id):
        if not isinstance(request_id, UUID):
            raise ValueError("exact workload request identity required")
        return self.directory / f"{request_id}.json"

    def _review(self, draft, receipt, draft_store):
        saved = draft_store.require_saved(draft)
        reviewed = WorkloadRequestReceipt.from_payload(receipt.payload(), draft=saved)
        return saved, reviewed

    def save(self, *, draft, receipt, trust, credential, draft_store):
        saved, reviewed = self._review(draft, receipt, draft_store)
        verified = verify_workload_credential(saved, reviewed, trust, service_account_delivery_payload(credential))
        try:
            commit_credential_generation(self.directory, self._pointer(saved.proposal.id), kind="integration",
                metadata=workload_credential_metadata(saved, reviewed, verified),
                certificate=(verified.certificate_pem.strip() + "\n" + verified.certificate_chain_pem.strip() + "\n").encode(),
                private_key=workload_key_pem(saved))
        except FileExistsError:
            pass  # Load and compare the committed candidate, never replace it.
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        stored = self.load(draft=saved, receipt=reviewed, trust=trust, draft_store=draft_store)
        if stored.credential != verified:
            raise OnboardingClientError("onboarding_workload_credential_selection_changed")
        return stored

    def load(self, *, draft, receipt, trust, draft_store):
        saved, reviewed = self._review(draft, receipt, draft_store)
        try:
            record = json.loads(read_private_file(self._pointer(saved.proposal.id), limit=4096))
            if (not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] != 1
                    or not isinstance(record.get("generation"), str)
                    or not re.fullmatch(r"integration-[0-9a-f]{32}", record["generation"])):
                raise ValueError("closed workload generation required")
            directory = self.directory / record["generation"]
            certificate, key = directory / "certificate.pem", directory / "private-key.pem"
            if read_private_file(key, limit=8192) != workload_key_pem(saved):
                raise ValueError("workload key differs from persisted proposal")
            material = parse_certificates(read_private_file(certificate, limit=65536).decode("ascii"), maximum=9)
            pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode()
            payload = {"operation_id": record["operation_id"], "status": "awaiting_activation",
                "service_account_id": record["service_account_id"], "integration_id": record["integration_id"],
                "certificate_pem": pem(material[0]), "certificate_chain_pem": "".join(pem(cert) for cert in material[1:]),
                "certificate_sha256": record["certificate_sha256"], "expires_at": record["expires_at"]}
            verified = verify_workload_credential(saved, reviewed, trust, payload,
                verification_time=retained_history_time(certificate, material[0]))
            if {name: value for name, value in record.items() if name != "generation"} != workload_credential_metadata(saved, reviewed, verified):
                raise ValueError("committed workload scope changed")
            return StoredWorkloadCredential(verified, certificate, key)
        except FileNotFoundError as exc:
            raise OnboardingClientError("onboarding_workload_credential_recovery_required") from exc
        except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
