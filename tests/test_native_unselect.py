"""Removing a local selection requires its original private request, not login."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from atellagent_client.cli import setup_native
from atellagent_client.onboarding import native_local_draft
from atellagent_client.onboarding.native_runtime_selection import NativeRuntimeSelections
from native_runtime_binding_support import runtime_case


@pytest.mark.asyncio
async def test_shipping_unselect_uses_original_draft_offline_and_retains_credentials(tmp_path, monkeypatch, capsys):
    runtime = runtime_case(tmp_path)
    draft = runtime.case.draft
    installation = SimpleNamespace(load=lambda: (
        SimpleNamespace(operation_id=draft.setup_id, tenant_id=draft.tenant_id,
            owner_user_id=draft.owner_user_id, platform_origin=draft.platform_origin),
        SimpleNamespace(device_id=draft.device_id), "unused"))
    monkeypatch.setattr(native_local_draft, "DeviceInstallation", lambda directory: installation)
    monkeypatch.setattr(native_local_draft, "NativeSetupStore", lambda directory: runtime.case.draft_store)
    monkeypatch.setattr(setup_native, "installation_directory", lambda: tmp_path)
    no_network = AsyncMock(side_effect=AssertionError("Local removal must not require server/browser access"))
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", no_network)
    monkeypatch.setattr(setup_native, "login", no_network)
    slots = NativeRuntimeSelections(tmp_path / "native-runtime-selections")
    slots.select(draft=draft, binding=runtime.binding)
    key = runtime.binding.stored.private_key_path
    before = key.read_bytes()
    await setup_native.run(["unselect", str(draft.proposal.id)])
    assert slots.load("codex") is None and key.read_bytes() == before
    assert runtime.case.draft_store.require_saved(draft).encode() == draft.encode()
    no_network.assert_not_awaited()
    assert "next selection check" in capsys.readouterr().out
