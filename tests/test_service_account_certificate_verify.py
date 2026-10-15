"""Returned service-account chains never authenticate themselves."""

from uuid import uuid4
import pytest

from atellagent_client.onboarding.errors import OnboardingClientError
from atellagent_client.onboarding.service_account_certificate_verify import verify_service_account_delivery
from service_account_certificate_support import certificate_case


def test_actual_chain_matches_independent_trust_and_retained_csr():
    case = certificate_case()
    result = verify_service_account_delivery(**case.args)
    assert result.service_account_id == case.args["service_account_id"]
    assert str(result.operation_id) == case.args["payload"]["operation_id"]
    assert result.certificate_sha256 == case.args["payload"]["certificate_sha256"]


@pytest.mark.parametrize("change", [{"server_auth": True}, {"ca_leaf": True}, {"wrong_dns": True},
                                    {"expired": True}, {"future": True}, {"days": 91}])
def test_invalid_leaf_purpose_identity_or_lifetime(change):
    with pytest.raises(OnboardingClientError, match="service_account_certificate_invalid"):
        verify_service_account_delivery(**certificate_case(**change).args)


@pytest.mark.parametrize("field", ["trust", "approved_public_key_der", "csr_pem"])
def test_foreign_trust_or_key_cannot_be_adopted(field):
    case, other = certificate_case(), certificate_case()
    case.args[field] = other.args[field]
    with pytest.raises(OnboardingClientError):
        verify_service_account_delivery(**case.args)


@pytest.mark.parametrize("change", [{"service_account_id": str(uuid4())}, {"integration_id": str(uuid4())},
    {"operation_id": "not-an-id"}, {"certificate_sha256": "0" * 64}, {"expires_at": "2099-01-01T00:00:00Z"},
    {"status": "connected"}, {"private_key_pem": "do not adopt"}])
def test_metadata_is_not_authority(change):
    case = certificate_case()
    case.args["payload"].update(change)
    with pytest.raises(OnboardingClientError):
        verify_service_account_delivery(**case.args)


@pytest.mark.parametrize("field", ["certificate_pem", "certificate_chain_pem"])
def test_extra_material_not_ignored(field):
    case = certificate_case()
    case.args["payload"][field] += "not a certificate"
    with pytest.raises(OnboardingClientError):
        verify_service_account_delivery(**case.args)


def test_unrelated_ca_in_returned_chain_rejected():
    case, other = certificate_case(), certificate_case()
    case.args["payload"]["certificate_chain_pem"] += other.args["trust"].ca_bundle_pem
    with pytest.raises(OnboardingClientError):
        verify_service_account_delivery(**case.args)
