"""Foreground startup uses the verified binding and cleans up failed starts."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atellagent_client.cli import native_runtime as module
from atellagent_client.cli.setup_native_options import options


def test_run_options_accept_exact_request_without_config():
    request_id = uuid4()
    args = options(["run", str(request_id), "--hook-control-socket", "/tmp/private.sock", "--verify"])
    assert args.request_id == request_id and args.verify
    assert not hasattr(args, "config")


@pytest.mark.asyncio
@pytest.mark.parametrize("fails", [False, True])
async def test_runtime_receives_binding_and_stops_after_success_or_failure(monkeypatch, fails):
    context, request_id = object(), uuid4()
    setup = SimpleNamespace(config=object(), binding=object())
    opening = AsyncMock(return_value=setup)
    runtime = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("start failed") if fails else None),
                              stop=AsyncMock(), socket_path="/tmp/private.sock")
    constructor = Mock(return_value=runtime)
    wait = AsyncMock()
    monkeypatch.setattr(module, "open_native_runtime", opening)
    monkeypatch.setattr(module, "HookControlRuntime", constructor)
    monkeypatch.setattr(module, "wait_for_shutdown_signal", wait)
    if fails:
        with pytest.raises(RuntimeError, match="start failed"):
            await module.run(context, request_id, socket_path=runtime.socket_path)
        wait.assert_not_awaited()
    else:
        await module.run(context, request_id, socket_path=runtime.socket_path)
        wait.assert_awaited_once()
    opening.assert_awaited_once_with(context, request_id)
    constructor.assert_called_once_with(setup.config, native_binding=setup.binding, socket_path=runtime.socket_path)
    runtime.stop.assert_awaited_once()


@pytest.mark.asyncio
async def test_foreground_private_installation_does_not_read_main_selector(monkeypatch, tmp_path):
    from atellagent_client.cli import setup_native
    request_id = uuid4()
    private = tmp_path / "installation"
    context = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    class Context:
        async def __aenter__(self):
            return context
        async def __aexit__(self, *_):
            return False
    opening = AsyncMock(return_value=Context())
    runner = AsyncMock()
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", opening)
    monkeypatch.setattr(setup_native, "installation_directory", lambda: pytest.fail("main selector read"))
    monkeypatch.setattr(module, "run", runner)
    await setup_native.run(["run", str(request_id), "--hook-control-socket", str(tmp_path / "private.sock"),
        "--installation-directory", str(private), "--verify"])
    opening.assert_awaited_once_with(private)
    runner.assert_awaited_once_with(context, request_id, socket_path=str(tmp_path / "private.sock"), verify=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["select", "consent", "submit", "hooks"])
async def test_private_foreground_path_cannot_redirect_mutating_commands(monkeypatch, action, tmp_path):
    from atellagent_client.cli import setup_native
    opening = AsyncMock(side_effect=AssertionError("device accessed"))
    monkeypatch.setattr(setup_native.NativeSetupContext, "open", opening)
    with pytest.raises(SystemExit) as result:
        await setup_native.run([action, str(uuid4()), "--installation-directory", str(tmp_path)])
    assert result.value.code == 2
    opening.assert_not_awaited()


def test_foreground_relative_installation_rejected_before_opening():
    with pytest.raises(SystemExit) as result:
        options(["run", str(uuid4()), "--hook-control-socket", "/private/tmp/private.sock",
                 "--installation-directory", "relative"])
    assert result.value.code == 2


@pytest.mark.asyncio
async def test_private_foreground_rejects_symlinked_installation(tmp_path):
    from atellagent_client.onboarding.native_setup_context import NativeSetupContext
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    linked = tmp_path / "linked"
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="ancestry|private"):
        await NativeSetupContext.open(linked)
