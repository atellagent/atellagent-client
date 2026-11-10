# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Durable setup drafts are saved before submission; reopening never generates keys."""

from pathlib import Path
from uuid import UUID

from .atomic_private_file import create_private_file, read_private_file
from .bootstrap_origin import bootstrap_origin
from .errors import OnboardingClientError
from .private_paths import create_private_directory
from .setup_draft import SetupDraft


class SetupStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)
        try:
            create_private_directory(self.directory)
        except (OSError, ValueError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def _path(self, operation_id: UUID) -> Path:
        if not isinstance(operation_id, UUID):
            raise ValueError("exact setup operation required")
        return self.directory / f"{operation_id}.json"

    def save_initial(self, draft: SetupDraft) -> None:
        try:
            create_private_file(self._path(draft.operation_id), draft.encode())
        except FileExistsError as exc:
            raise OnboardingClientError("onboarding_setup_already_saved") from exc
        except (OSError, ValueError, TypeError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def load(self, operation_id: UUID, *, expected_origin: str,
        expected_tenant_id: UUID, expected_owner_user_id: UUID) -> SetupDraft:
        try:
            draft = SetupDraft.decode(read_private_file(self._path(operation_id), limit=32768))
            if (draft.operation_id != operation_id or draft.platform_origin != bootstrap_origin(expected_origin)
                or draft.tenant_id != expected_tenant_id or draft.owner_user_id != expected_owner_user_id):
                raise ValueError("setup ownership/identity changed")
            return draft
        except (OSError, ValueError, TypeError, KeyError) as exc:
            raise OnboardingClientError("onboarding_storage_unavailable") from exc

    def require_saved(self, draft: SetupDraft, *, expected_origin: str) -> SetupDraft:
        saved = self.load(draft.operation_id, expected_origin=expected_origin,
            expected_tenant_id=draft.tenant_id, expected_owner_user_id=draft.owner_user_id)
        if saved.encode() != draft.encode():
            raise OnboardingClientError("onboarding_setup_intent_changed")
        return saved
