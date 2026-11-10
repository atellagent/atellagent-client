"""Membership selection and creation receipts do not imply approval or readiness."""

from uuid import uuid4

import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.setup_identity import SetupIdentity
from atellagent_client.onboarding.setup_receipt import SetupReceipt


def test_selection_is_required_even_for_one_membership():
    owner, tenant = uuid4(), uuid4()
    result = SetupIdentity.from_payload({"user_id": str(owner), "selection_required": True,
        "memberships": [{"tenant_id": str(tenant), "tenant_name": "Customer", "can_approve": False}]})
    result.require_selection(expected_user_id=owner, tenant_id=tenant)
    with pytest.raises(OnboardingClientError, match="account_changed"):
        result.require_selection(expected_user_id=uuid4(), tenant_id=tenant)
    with pytest.raises(OnboardingClientError, match="membership_required"):
        result.require_selection(expected_user_id=owner, tenant_id=uuid4())


@pytest.mark.parametrize("change", [{"selection_required": False}, {"selection_required": 1},
    {"unexpected": "field"}, {"memberships": "Customer"}, {"user_id": "unknown"}])
def test_identity_malformed_or_implicit_selection_rejected(change):
    payload = {"user_id": str(uuid4()), "selection_required": True, "memberships": []}
    with pytest.raises(OnboardingClientError, match="identity_invalid"):
        SetupIdentity.from_payload({**payload, **change})


def receipt(operation):
    return {"id": str(operation), "kind": "device", "status": "pending", "device_id": str(uuid4()),
        "revision": 1, "expires_at": "2026-10-08T12:00:00Z", "claim_expires_at": None}


def test_pending_receipt_retains_server_assigned_device_without_claim_authority():
    operation = uuid4()
    result = SetupReceipt.from_payload(receipt(operation), operation_id=operation, kind="device")
    assert result.status == "pending" and result.claim_expires_at is None


@pytest.mark.parametrize("change", [{"revision": True}, {"revision": 2}, {"status": "connected"},
    {"device_id": None}, {"id": str(uuid4())}, {"kind": "workload"},
    {"status": "approved"}, {"expires_at": "2026-10-08T12:00:00"}, {"credentials": "secret"}])
def test_changed_or_unbounded_receipts_are_rejected(change):
    operation = uuid4()
    with pytest.raises(OnboardingClientError, match="response_invalid"):
        SetupReceipt.from_payload({**receipt(operation), **change}, operation_id=operation, kind="device")
