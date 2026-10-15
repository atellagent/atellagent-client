# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Local activation completion is durable history, not continuing server authority."""

import json

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .proofs import canonical_json


def _record(draft, stored) -> dict:
    return {"revision": 1, "status": "device_ready", "setup_id": str(draft.operation_id),
        "tenant_id": str(draft.tenant_id), "owner_user_id": str(draft.owner_user_id),
        "device_id": str(stored.credential.device_id), "platform_origin": draft.platform_origin,
        "certificate_sha256": stored.credential.certificate_sha256}


def require_activation_receipt(draft, stored) -> None:
    try:
        payload = json.loads(read_private_file(stored.certificate_path.parent / "activation.json", limit=4096))
        if not isinstance(payload, dict) or type(payload.get("revision")) is not int or payload != _record(draft, stored):
            raise ValueError("exact activation receipt required")
    except FileNotFoundError as exc:
        raise OnboardingClientError("onboarding_activation_receipt_missing") from exc
    except (OSError, ValueError, TypeError) as exc:
        raise OnboardingClientError("onboarding_activation_receipt_unavailable") from exc


def save_activation_receipt(draft, stored) -> None:
    try:
        create_private_file(stored.certificate_path.parent / "activation.json", canonical_json(_record(draft, stored)))
    except FileExistsError:
        pass  # Existing immutable receipt must match; mutable reporting state is not recorded.
    except (OSError, ValueError) as exc:
        raise OnboardingClientError("onboarding_activation_receipt_unavailable") from exc
    require_activation_receipt(draft, stored)
