"""Host slots pin exact retained requests and never adopt other local state."""

from dataclasses import replace
from uuid import uuid4

import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from native_runtime_binding_support import runtime_case


def test_exact_selection_is_idempotent_and_another_request_cannot_replace_it(tmp_path):
    runtime = runtime_case(tmp_path)
    slots = NativeRuntimeSelections(tmp_path / "slots")
    assert slots.load("codex") is None
    for _ in range(2):
        slots.select(draft=runtime.case.draft, binding=runtime.binding)
    slots.require_draft(slots.load("codex"), runtime.case.draft)
    other = replace(runtime.case.draft, proposal=replace(runtime.case.draft.proposal, id=uuid4()))
    with pytest.raises(OnboardingClientError, match="onboarding_native_host_already_selected"):
        slots.select(draft=other, binding=replace(runtime.binding, request_id=other.proposal.id))
    assert slots.load("codex")["request_id"] == runtime.binding.request_id


def test_cross_installation_draft_is_not_selected_or_loaded(tmp_path):
    runtime = runtime_case(tmp_path)
    slots = NativeRuntimeSelections(tmp_path / "slots")
    with pytest.raises(OnboardingClientError, match="onboarding_native_selection_invalid"):
        slots.select(draft=replace(runtime.case.draft, device_id=uuid4()), binding=runtime.binding)
    slots.select(draft=runtime.case.draft, binding=runtime.binding)
    with pytest.raises(OnboardingClientError, match="onboarding_native_selection_invalid"):
        slots.require_draft(slots.load("codex"), replace(runtime.case.draft, setup_id=uuid4()))


def test_unsafe_slot_is_rejected_not_treated_as_unconfigured(tmp_path):
    slots = NativeRuntimeSelections(tmp_path / "slots")
    (slots.directory / "codex.json").symlink_to(tmp_path / "missing")
    with pytest.raises(OnboardingClientError, match="onboarding_native_selection_unavailable"):
        slots.load("codex")


def test_exact_removal_retains_keys_and_cannot_remove_a_replacement(tmp_path):
    runtime = runtime_case(tmp_path)
    slots = NativeRuntimeSelections(tmp_path / "slots")
    original = runtime.case.draft
    slots.select(draft=original, binding=runtime.binding)
    slots.remove(draft=original)
    assert slots.load("codex") is None
    slots.remove(draft=original)
    assert runtime.binding.stored.private_key_path.is_file()
    replacement = replace(original, proposal=replace(original.proposal, id=uuid4()))
    slots.select(draft=replacement, binding=replace(runtime.binding, request_id=replacement.proposal.id))
    with pytest.raises(OnboardingClientError, match="onboarding_native_selection_invalid"):
        slots.remove(draft=original)
    assert slots.load("codex")["request_id"] == replacement.proposal.id


def test_selection_mutations_require_the_owned_process_lease(tmp_path):
    from atellagent_client.onboarding.process_lease import process_lease
    runtime = runtime_case(tmp_path)
    slots = NativeRuntimeSelections(tmp_path / "slots")
    slots.select(draft=runtime.case.draft, binding=runtime.binding)
    with process_lease(slots.directory / ".selection.lock") as held:
        assert held
        with pytest.raises(OnboardingClientError, match="onboarding_native_selection_busy"):
            slots.remove(draft=runtime.case.draft)
        with pytest.raises(OnboardingClientError, match="onboarding_native_selection_busy"):
            slots.select(draft=runtime.case.draft, binding=runtime.binding)
    assert slots.load("codex") is not None
