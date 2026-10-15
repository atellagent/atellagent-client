# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Atomic integration generations retain the pre-submission key and exact intent."""

import base64
from hashlib import sha256
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import serialization

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .credential_generation import commit_credential_generation
from .device_assignments import DeviceAssignment
from .errors import OnboardingClientError
from .native_credential_record import StoredNativeCredential
from .service_account_delivery_payload import service_account_delivery_payload
from .private_paths import create_private_directory
from .service_account_certificate_verify import verify_service_account_delivery
from .service_account_csr import service_account_csr
from .renewed_credential import retained_history_time


class NativeCredentialStore:
    def select(self, *, draft, assignment, trust, fingerprint, draft_store):
        from .native_credential_selection import select_native_credential
        return select_native_credential(self, draft=draft, assignment=assignment, trust=trust,
                                        fingerprint=fingerprint, draft_store=draft_store)

    def load_selected(self, *, draft, assignment, trust, draft_store):
        from .native_credential_selection import load_selected_native_credential
        return load_selected_native_credential(self, draft=draft, assignment=assignment,
                                              trust=trust, draft_store=draft_store)

    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _pointer(self, draft, fingerprint):
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("verified credential fingerprint required")
        return self.directory / f"{draft.proposal.id}-{fingerprint}.json"

    def _review(self, draft, assignment, draft_store):
        saved = draft_store.require_saved(draft)
        try:
            checked = DeviceAssignment.from_payload(assignment.payload(), device=saved)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise OnboardingClientError("onboarding_native_setup_intent_changed") from exc
        if checked.proposal != saved.proposal or checked.onboarding_session_id != saved.setup_id:
            raise OnboardingClientError("onboarding_native_setup_intent_changed")
        return saved, checked

    def _verify(self, draft, assignment, trust, payload, *, verification_time=None):
        return verify_service_account_delivery(platform_origin=draft.platform_origin, tenant_id=draft.tenant_id,
            service_account_id=assignment.service_account_id, integration_id=assignment.integration_id,
            csr_pem=service_account_csr(draft.tls_key, tenant_id=draft.tenant_id,
                                      service_account_id=assignment.service_account_id),
            approved_public_key_der=base64.b64decode(draft.public_key_base64(), validate=True), trust=trust, payload=payload,
            verification_time=verification_time)

    def _metadata(self, draft, assignment, credential):
        return {"revision": 1, "request_id": str(draft.proposal.id), "setup_id": str(draft.setup_id),
            "tenant_id": str(draft.tenant_id), "device_id": str(draft.device_id), "owner_user_id": str(draft.owner_user_id),
            "platform_origin": draft.platform_origin, "draft_sha256": sha256(draft.encode()).hexdigest(),
            "intent_sha256": assignment.intent_sha256, "operation_id": str(credential.operation_id),
            "service_account_id": str(credential.service_account_id), "integration_id": str(credential.integration_id),
            "certificate_sha256": credential.certificate_sha256, "expires_at": credential.expires_at.isoformat()}

    def save(self, *, draft, assignment, trust, credential, draft_store):
        saved, reviewed = self._review(draft, assignment, draft_store)
        verified = self._verify(saved, reviewed, trust, service_account_delivery_payload(credential))
        try:
            commit_credential_generation(self.directory, self._pointer(saved, verified.certificate_sha256),
                kind="integration", metadata=self._metadata(saved, reviewed, verified),
                certificate=(verified.certificate_pem.strip() + "\n" + verified.certificate_chain_pem.strip() + "\n").encode(),
                private_key=saved.private_key_pem().encode())
        except FileExistsError:
            pass  # Revalidate the exact committed generation; never overwrite it.
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        return self.load(draft=saved, assignment=reviewed, trust=trust,
                         fingerprint=verified.certificate_sha256, draft_store=draft_store)

    def load(self, *, draft, assignment, trust, fingerprint, draft_store):
        saved, reviewed = self._review(draft, assignment, draft_store)
        try:
            record = json.loads(read_private_file(self._pointer(saved, fingerprint), limit=4096))
            if (not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] != 1
                    or record.get("certificate_sha256") != fingerprint
                    or not isinstance(record.get("generation"), str)
                    or not re.fullmatch(r"integration-[0-9a-f]{32}", record["generation"])):
                raise ValueError("closed integration generation required")
            directory = self.directory / record["generation"]
            certificate, key = directory / "certificate.pem", directory / "private-key.pem"
            if read_private_file(key, limit=8192) != saved.private_key_pem().encode():
                raise ValueError("stored key differs from retained native draft")
            material = parse_certificates(read_private_file(certificate, limit=65536).decode("ascii"), maximum=9)
            pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode()
            payload = {"operation_id": record["operation_id"], "status": "awaiting_activation",
                "service_account_id": record["service_account_id"], "integration_id": record["integration_id"],
                "certificate_pem": pem(material[0]), "certificate_chain_pem": "".join(pem(cert) for cert in material[1:]),
                "certificate_sha256": record["certificate_sha256"], "expires_at": record["expires_at"]}
            verified = self._verify(saved, reviewed, trust, payload,
                verification_time=retained_history_time(certificate, material[0]))
            if {name: value for name, value in record.items() if name != "generation"} != self._metadata(saved, reviewed, verified):
                raise ValueError("committed integration scope changed")
            return StoredNativeCredential(verified, certificate, key)
        except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
