"""Facade startup resumes approved state and cleans up failed listeners."""

import argparse
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from atellagent_client.integrations.agents import facade_cli as module


def arguments():
    return SimpleNamespace(request_id=uuid4(), listen="127.0.0.1:8787", token_file="/private/token")


@pytest.mark.asyncio
@pytest.mark.parametrize("startup_failure", [False, True])
async def test_saved_request_identity_supplies_runtime_and_is_closed(monkeypatch, startup_failure):
    args = arguments()
    context = SimpleNamespace(__aenter__=AsyncMock(), __aexit__=AsyncMock())
    class Scope:
        async def __aenter__(self):
            return context
        async def __aexit__(self, *values):
            await context.__aexit__(*values)
    opening = AsyncMock(return_value=Scope())
    monkeypatch.setattr(module.NativeSetupContext, "open", opening)
    setup = SimpleNamespace(config=object(), binding=object())
    resume = AsyncMock(return_value=setup)
    monkeypatch.setattr(module, "open_native_runtime", resume)
    runtime = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError("listener") if startup_failure else None),
        stop=AsyncMock(), run_forever=AsyncMock(), address=("127.0.0.1", 8787))
    factory = Mock(return_value=runtime)
    if startup_failure:
        with pytest.raises(RuntimeError, match="listener"):
            await module.run_facade(args, factory, "Test", lambda _: "t" * 32)
    else:
        await module.run_facade(args, factory, "Test", lambda _: "t" * 32)
        runtime.run_forever.assert_awaited_once()
    resume.assert_awaited_once_with(context, args.request_id)
    factory.assert_called_once_with(setup.config, native_binding=setup.binding,
        capability_token="t" * 32, host="127.0.0.1", port=8787)
    runtime.stop.assert_awaited_once()
    context.__aexit__.assert_awaited_once()


def test_cli_requires_request_id_and_has_no_yaml_argument():
    parser = argparse.ArgumentParser()
    module.add_request_option(parser)
    request = uuid4()
    assert parser.parse_args(["--request-id", str(request)]).request_id == request
    with pytest.raises(SystemExit):
        parser.parse_args(["old.yaml"])
