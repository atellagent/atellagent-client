# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Pin the initial verified credential before sending native activation."""

import json

from .atomic_private_file import create_private_file, read_private_file
from .errors import OnboardingClientError
from .proofs import canonical_json


def _path(store, draft):
    return store.directory / f"{draft.proposal.id}-selected.json"


def _record(store, draft, assignment, credential):
    return store._metadata(draft, assignment, credential)


def select_native_credential(store, *, draft, assignment, trust, fingerprint, draft_store):
    saved, reviewed = store._review(draft, assignment, draft_store)
    credential = store.load(draft=saved, assignment=reviewed, trust=trust,
                            fingerprint=fingerprint, draft_store=draft_store)
    expected = _record(store, saved, reviewed, credential.credential)
    try:
        create_private_file(_path(store, saved), canonical_json(expected))
    except FileExistsError:
        pass  # Retry only the same initial selection, never replace authority.
    except (OSError, ValueError) as exc:
        raise OnboardingClientError("onboarding_storage_unavailable") from exc
    selected = load_selected_native_credential(store, draft=saved, assignment=reviewed,
                                               trust=trust, draft_store=draft_store)
    if selected.credential != credential.credential:
        raise OnboardingClientError("onboarding_native_credential_selection_changed")
    return selected


def load_selected_native_credential(store, *, draft, assignment, trust, draft_store):
    saved, reviewed = store._review(draft, assignment, draft_store)
    try:
        record = json.loads(read_private_file(_path(store, saved), limit=4096))
        if not isinstance(record, dict) or type(record.get("revision")) is not int or record["revision"] != 1:
            raise ValueError("closed initial selection required")
        credential = store.load(draft=saved, assignment=reviewed, trust=trust,
                                fingerprint=record["certificate_sha256"], draft_store=draft_store)
        if record != _record(store, saved, reviewed, credential.credential):
            raise ValueError("initial selection scope changed")
        return credential
    except FileNotFoundError as exc:
        raise OnboardingClientError("onboarding_native_credential_recovery_required") from exc
    except (OSError, ValueError, TypeError, KeyError) as exc:
        raise OnboardingClientError("onboarding_storage_unavailable") from exc
