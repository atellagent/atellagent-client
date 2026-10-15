# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Resume revalidates exact persisted credentials, never reconstructs a partial pair."""

from hashlib import sha256
from datetime import timedelta
import json
from pathlib import Path
import re

from cryptography.hazmat.primitives import serialization

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .device_certificate_verify import verify_device_delivery
from .credential_generation import commit_credential_generation
from .device_credential_record import StoredDeviceCredential, delivery_payload
from .device_csr import device_certificate_operation_id
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .setup_key_encoding import encode_keys


class DeviceCredentialStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _pointer(self, draft, fingerprint):
        if not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint):
            raise ValueError("verified certificate fingerprint required")
        return self.directory / f"{draft.operation_id}-{fingerprint}.json"

    def _metadata(self, draft, credential):
        return {"revision": 1, "setup_id": str(draft.operation_id), "tenant_id": str(draft.tenant_id),
            "owner_user_id": str(draft.owner_user_id), "device_id": str(credential.device_id),
            "platform_origin": draft.platform_origin, "draft_sha256": sha256(draft.encode()).hexdigest(),
            "certificate_sha256": credential.certificate_sha256, "expires_at": credential.expires_at.isoformat()}

    def save(self, *, draft, receipt, trust, credential, setup_store) -> StoredDeviceCredential:
        saved = setup_store.require_saved(draft, expected_origin=trust.platform_origin)
        verified = verify_device_delivery(draft=saved, receipt=receipt, trust=trust,
            payload=delivery_payload(saved, credential))
        try:
            commit_credential_generation(self.directory, self._pointer(saved, verified.certificate_sha256), kind="device",
                metadata=self._metadata(saved, verified),
                certificate=(verified.certificate_pem.strip() + "\n" + verified.certificate_chain_pem.strip() + "\n").encode(),
                private_key=encode_keys(saved.keys)["tls_key_pem"].encode())
        except FileExistsError:
            pass  # Same current-format commit is checked below; never overwritten.
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        return self.load(draft=saved, receipt=receipt, trust=trust, fingerprint=verified.certificate_sha256)

    def load(self, *, draft, receipt, trust, fingerprint) -> StoredDeviceCredential:
        return self._load(draft=draft, receipt=receipt, trust=trust, fingerprint=fingerprint, history_only=False)

    def load_history(self, *, draft, receipt, trust, fingerprint) -> StoredDeviceCredential:
        """Original activation history only; never a current TLS authority decision."""
        return self._load(draft=draft, receipt=receipt, trust=trust, fingerprint=fingerprint, history_only=True)

    def _load(self, *, draft, receipt, trust, fingerprint, history_only) -> StoredDeviceCredential:
        try:
            record = json.loads(read_private_file(self._pointer(draft, fingerprint), limit=4096))
            expected = {"revision", "setup_id", "tenant_id", "owner_user_id", "device_id", "platform_origin",
                "draft_sha256", "certificate_sha256", "expires_at", "generation"}
            if (not isinstance(record, dict) or set(record) != expected or type(record["revision"]) is not int
                or record["revision"] != 1 or record["certificate_sha256"] != fingerprint
                or not isinstance(record["generation"], str)
                or not re.fullmatch(r"device-[0-9a-f]{32}", record["generation"])):
                raise ValueError("closed committed generation required")
            directory = self.directory / record["generation"]
            certificate, key = directory / "certificate.pem", directory / "private-key.pem"
            material = parse_certificates(read_private_file(certificate, limit=65536).decode("ascii"), maximum=9)
            if read_private_file(key, limit=8192) != encode_keys(draft.keys)["tls_key_pem"].encode():
                raise ValueError("persisted TLS key differs from frozen setup")
            pem = lambda cert: cert.public_bytes(serialization.Encoding.PEM).decode()
            payload = {"operation_id": str(device_certificate_operation_id(draft.operation_id)),
                "status": "awaiting_activation", "device_id": record["device_id"], "certificate_pem": pem(material[0]),
                "certificate_chain_pem": "".join(pem(cert) for cert in material[1:]),
                "certificate_sha256": record["certificate_sha256"], "expires_at": record["expires_at"]}
            verified = verify_device_delivery(draft=draft, receipt=receipt, trust=trust, payload=payload,
                verification_time=material[0].not_valid_before_utc + timedelta(seconds=1) if history_only else None)
            if {name: value for name, value in record.items() if name != "generation"} != self._metadata(draft, verified):
                raise ValueError("committed device identity changed")
            return StoredDeviceCredential(verified, certificate, key)
        except (OSError, ValueError, TypeError, KeyError, IndexError, UnicodeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
