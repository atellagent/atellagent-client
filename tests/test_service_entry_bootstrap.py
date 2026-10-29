# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Windowless service startup ownership and non-interactive failure semantics."""
import asyncio
import logging
import os
from pathlib import Path
import sys
from types import SimpleNamespace,ModuleType

import pytest

from atellagent_client.cli import service_entry as entry
from atellagent_client.cli import windows_service
from atellagent_client.onboarding.private_paths import require_private_path


@pytest.fixture
def isolated_entry(monkeypatch,tmp_path):
    local=tmp_path/'Local';local.mkdir()
    streams=SimpleNamespace(stdin=None,stdout=None,stderr=None)
    state=SimpleNamespace(local=local,root=local/'Atellagent',failure=None,events=[],protected=[])
    handlers=[]
    def configure(**kwargs):handlers.extend(kwargs['handlers'])
    monkeypatch.setattr(entry,'os',SimpleNamespace(name='nt',environ={'LOCALAPPDATA':str(local)},devnull=os.devnull))
    monkeypatch.setattr(entry,'sys',streams)
    monkeypatch.setattr(entry,'logging',SimpleNamespace(WARNING=logging.WARNING,basicConfig=configure,
        warning=lambda message:state.events.append(('warning',message)),
        exception=lambda message:state.events.append(('exception',message))))
    def protect(path):
        state.protected.append(path)
        path.chmod(0o700 if path.is_dir()else 0o600)
    monkeypatch.setattr(windows_service,'secure_user_path',protect)
    supervisor=ModuleType('atellagent_client.cli.supervisor')
    async def run():
        state.events.append(('run',None))
        require_private_path(state.root,directory=True)
        require_private_path(state.root/'services',directory=True)
        if state.failure is not None:raise state.failure
    supervisor.run=run
    monkeypatch.setitem(sys.modules,'atellagent_client.cli.supervisor',supervisor)
    try:yield state
    finally:
        for handler in handlers:handler.close()
        for stream in {id(value):value for value in vars(streams).values()if value is not None}.values():stream.close()


def test_cold_bootstrap_creates_private_parent_before_supervisor(isolated_entry):
    state=isolated_entry
    assert entry.main()is None
    assert ('run',None)in state.events
    assert not(state.root/'installation'/'installation.json').exists()
    assert state.protected==[state.root/'services'/'supervisor.log']


def test_background_failure_exits_nonzero_without_rethrowing_exception(isolated_entry):
    state=isolated_entry
    state.root.mkdir(mode=0o700);(state.root/'services').mkdir(mode=0o700)
    state.failure=RuntimeError('synthetic startup failure')
    with pytest.raises(SystemExit)as error:entry.main()
    assert error.value.code==1
    assert ('exception','Local controller supervisor exited unexpectedly')in state.events


@pytest.mark.parametrize('component',['parent','services'])
def test_existing_unsafe_directory_is_rejected_without_repair(isolated_entry,component):
    state=isolated_entry
    state.root.mkdir(mode=0o700 if component=='services'else 0o755)
    bad=state.root
    if component=='services':bad=state.root/'services';bad.mkdir(mode=0o755)
    with pytest.raises(ValueError):entry.main()
    assert bad.stat().st_mode&0o777==0o755
    assert not state.protected and ('run',None)not in state.events


def test_linked_log_is_not_opened_or_repaired(isolated_entry,tmp_path):
    state=isolated_entry
    state.root.mkdir(mode=0o700);services=state.root/'services';services.mkdir(mode=0o700)
    target=tmp_path/'synthetic-target';target.write_text('unchanged')
    (services/'supervisor.log').symlink_to(target)
    with pytest.raises(RuntimeError,match='symlinked service log'):entry.main()
    assert target.read_text()=='unchanged'and not state.protected


def test_shutdown_cancellation_is_not_converted_to_startup_failure(isolated_entry):
    state=isolated_entry
    state.root.mkdir(mode=0o700);(state.root/'services').mkdir(mode=0o700)
    state.failure=asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):entry.main()
    assert not any(kind=='exception'for kind,_ in state.events)
