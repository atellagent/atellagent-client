"""Rotation uses the same independently anchored chain policy as enrollment."""

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.x509.verification import VerificationError
from atellagent_client.onboarding.certificate_material import verify_client_chain
from .certificate_errors import CertificateMaterialError


def validate_chain(certificate, chain_pem, trusted_ca_pem):
    try:
        verify_client_chain(certificate, chain_pem, trusted_ca_pem)
    except (ValueError, TypeError, UnicodeError, UnsupportedAlgorithm, VerificationError) as exc:
        raise CertificateMaterialError("Certificate response chain is not independently trusted") from exc
