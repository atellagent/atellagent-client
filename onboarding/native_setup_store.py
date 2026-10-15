# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Private immutable native drafts; retries reload instead of generating keys."""

from pathlib import Path
from uuid import UUID
from cryptography.exceptions import UnsupportedAlgorithm

from .atomic_private_file import create_private_file, read_private_file
from .bootstrap_origin import bootstrap_origin
from .errors import OnboardingClientError
from .native_setup_draft import NativeSetupDraft
from .private_paths import create_private_directory


class NativeSetupStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _path(self, request_id):
        if not isinstance(request_id, UUID):
            raise ValueError("exact native request identity required")
        return self.directory / f"{request_id}.json"

    def save_initial(self, draft: NativeSetupDraft):
        try:
            create_private_file(self._path(draft.proposal.id), draft.encode())
        except FileExistsError as exc:
            raise OnboardingClientError("onboarding_native_setup_already_saved") from exc
        except (OSError, ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def load(self, request_id, *, device, setup_id, expected_origin):
        try:
            draft = NativeSetupDraft.decode(read_private_file(self._path(request_id), limit=32768))
            if (draft.proposal.id != request_id or draft.setup_id != setup_id
                    or (draft.tenant_id, draft.device_id, draft.owner_user_id) != (
                        device.tenant_id, device.device_id, device.owner_user_id)
                    or draft.platform_origin != bootstrap_origin(expected_origin)):
                raise ValueError("native draft belongs to another exact installation")
            return draft
        except (OSError, ValueError, TypeError, KeyError, AttributeError, UnsupportedAlgorithm) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def require_saved(self, draft: NativeSetupDraft):
        saved = self.load(draft.proposal.id, device=draft, setup_id=draft.setup_id,
                          expected_origin=draft.platform_origin)
        if saved.encode() != draft.encode():
            raise OnboardingClientError("onboarding_native_setup_intent_changed")
        return saved

    def load_if_present(self, request_id, *, device, setup_id, expected_origin):
        """Only an absent file is a new draft; damaged/private state fails closed."""
        try:
            read_private_file(self._path(request_id), limit=32768)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc
        return self.load(request_id, device=device, setup_id=setup_id, expected_origin=expected_origin)
