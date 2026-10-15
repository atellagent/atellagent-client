"""Activation replay requires a confirmed, still-issued operation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from atellagent_client.connected.participant_rotation import ConnectedCertificateRotationMixin
from atellagent_client.connected.participant_protocol import ConnectedHTTPError


def setup_rotation(responses):
    participant = ConnectedCertificateRotationMixin()
    participant.config = SimpleNamespace(
        certificate_rotation_activation_path_template='/activate',
        certificate_rotation_operation_path_template='/status',
    )
    participant._rotation_request = AsyncMock(side_effect=responses)
    message = SimpleNamespace(message_id='message', lease=SimpleNamespace(
        lease_id='lease', lease_token='test-proof'))
    return participant, message


def state(status='issued', operation_id='operation'):
    return SimpleNamespace(json=lambda: {'operation_id': operation_id, 'status': status})


@pytest.mark.asyncio
async def test_activation_retries_same_operation_and_lease_after_confirmed_rollback():
    success = object()
    participant, message = setup_rotation([ConnectedHTTPError(500, 'failed'), state(), success])
    with patch('atellagent_client.connected.participant_rotation.asyncio.sleep', new=AsyncMock()):
        result = await participant._activate_issued_rotation(
            message, 'operation', datetime.now(timezone.utc) + timedelta(minutes=1))
    assert result is success
    calls = participant._rotation_request.await_args_list
    assert [call.args[0] for call in calls] == ['POST', 'GET', 'POST']
    assert calls[0] == calls[2]


@pytest.mark.asyncio
@pytest.mark.parametrize('status,operation_id', [('succeeded','operation'), ('issued','other')])
async def test_activation_is_not_replayed_without_matching_issued_state(status, operation_id):
    participant, message = setup_rotation([ConnectedHTTPError(500, 'failed'), state(status, operation_id)])
    with pytest.raises(ConnectedHTTPError):
        await participant._activate_issued_rotation(
            message, 'operation', datetime.now(timezone.utc) + timedelta(minutes=1))
    assert participant._rotation_request.await_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize('error', [ConnectedHTTPError(409, 'lease expired'), httpx.ReadTimeout('ambiguous')])
async def test_rejected_or_ambiguous_activation_is_not_replayed(error):
    participant, message = setup_rotation([error])
    with pytest.raises(type(error)):
        await participant._activate_issued_rotation(
            message, 'operation', datetime.now(timezone.utc) + timedelta(minutes=1))
    assert participant._rotation_request.await_count == 1


@pytest.mark.asyncio
async def test_retries_are_bounded():
    participant, message = setup_rotation([
        ConnectedHTTPError(500, 'failed'), state(),
        ConnectedHTTPError(503, 'failed'), state(), ConnectedHTTPError(500, 'failed')])
    with patch('atellagent_client.connected.participant_rotation.asyncio.sleep', new=AsyncMock()):
        with pytest.raises(ConnectedHTTPError):
            await participant._activate_issued_rotation(
                message, 'operation', datetime.now(timezone.utc) + timedelta(minutes=1))
    assert participant._rotation_request.await_count == 5


@pytest.mark.asyncio
async def test_no_retry_after_deadline():
    participant, message = setup_rotation([ConnectedHTTPError(500, 'failed')])
    with pytest.raises(ConnectedHTTPError):
        await participant._activate_issued_rotation(message, 'operation', datetime.now(timezone.utc))
    assert participant._rotation_request.await_count == 1
