# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Current installation selector; fixed owned layout, no imported configuration."""

import json
from pathlib import Path
import re
from uuid import UUID

from .atomic_private_file import create_private_file, read_private_file
from .bootstrap_origin import bootstrap_origin
from .device_activation_receipt import require_activation_receipt
from .device_credential_store import DeviceCredentialStore
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .setup_receipt import SetupReceipt
from .setup_store import SetupStore
from .receipt_encoding import receipt_payload


class DeviceInstallation:
    def __init__(self, directory: Path):
        self.directory = directory
        create_private_directory(directory)
        self.setups = SetupStore(directory / "setups")
        self.credentials = DeviceCredentialStore(directory / "credentials")
        self.path = directory / "installation.json"

    def save(self, *, draft, receipt, trust, fingerprint):
        saved = self.setups.require_saved(draft, expected_origin=trust.platform_origin)
        stored = self.credentials.load(draft=saved, receipt=receipt, trust=trust, fingerprint=fingerprint)
        require_activation_receipt(saved, stored)
        payload = {"revision": 1, "platform_origin": saved.platform_origin,
            "tenant_id": str(saved.tenant_id), "owner_user_id": str(saved.owner_user_id),
            "setup_id": str(saved.operation_id), "certificate_sha256": fingerprint,
            "receipt": receipt_payload(receipt)}
        content = json.dumps(payload, sort_keys=True).encode()
        try:
            create_private_file(self.path, content)
        except FileExistsError:
            current, assigned, selected = self.load()
            if (current.encode() != saved.encode() or selected != fingerprint
                or (assigned.id, assigned.kind, assigned.device_id, assigned.revision)
                != (receipt.id, receipt.kind, receipt.device_id, receipt.revision)):
                raise OnboardingClientError("device_installation_already_selected")

    def metadata(self):
        """Selector identity only; no credential or network authority claim."""
        try:
            value = json.loads(read_private_file(self.path, limit=4096))
            if (not isinstance(value, dict) or set(value) != {"revision", "platform_origin", "tenant_id",
                "owner_user_id", "setup_id", "certificate_sha256", "receipt"}
                or type(value["revision"]) is not int or value["revision"] != 1
                or not isinstance(value["certificate_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", value["certificate_sha256"])):
                raise ValueError("closed device installation required")
            origin = bootstrap_origin(value["platform_origin"])
            identifiers = {key: UUID(value[key]) for key in ("tenant_id", "owner_user_id", "setup_id")}
            if any(str(item) != value[key] for key, item in identifiers.items()):
                raise ValueError("canonical installation identity required")
            receipt = SetupReceipt.from_payload(value["receipt"], operation_id=identifiers["setup_id"], kind="device")
            if receipt.device_id is None:
                raise ValueError("selected device identity required")
            return origin, identifiers, receipt, value["certificate_sha256"]
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("device_installation_unavailable") from exc

    def load(self):
        origin, identifiers, receipt, fingerprint = self.metadata()
        draft = self.setups.load(identifiers["setup_id"], expected_origin=origin,
            expected_tenant_id=identifiers["tenant_id"], expected_owner_user_id=identifiers["owner_user_id"])
        return draft, receipt, fingerprint
