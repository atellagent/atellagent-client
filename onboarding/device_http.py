# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Dedicated device mTLS pool; never an agent/OAuth client's pool or credentials."""

from datetime import datetime, timezone
from pathlib import Path
import ssl
from uuid import UUID

from cryptography import x509
from cryptography.x509.oid import ExtendedKeyUsageOID
import httpx

from .errors import OnboardingClientError
from .credential_paths import require_device_credential_paths


def create_device_http_client(*, tenant_id: UUID, device_id: UUID,
    certificate_path: Path, private_key_path: Path, server_ca_path: Path | None = None) -> httpx.AsyncClient:
    """Check local credential purpose; remote admission remains server authority.

    The client credential was independently verified during onboarding. Its chain
    is not a server-trust bundle. Server authentication uses system roots or the
    deployment's separately trusted server CA, never roots from an issuer response.
    """
    try:
        require_device_credential_paths(certificate_path, private_key_path)
        with certificate_path.open("rb") as handle:
            encoded = handle.read(65537)
        if len(encoded) > 65536:
            raise ValueError("oversized credential")
        leaf = x509.load_pem_x509_certificates(encoded)[0]
        expected = f"device.{device_id.hex}.{tenant_id.hex}.identity.invalid"
        names = leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        eku = leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
        constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
        if list(names) != [x509.DNSName(expected)] or list(eku) != [ExtendedKeyUsageOID.CLIENT_AUTH] or constraints.ca:
            raise ValueError("device credential purpose mismatch")
        now = datetime.now(timezone.utc)
        if now < leaf.not_valid_before_utc:
            raise OnboardingClientError("device_credential_not_current")
        if now >= leaf.not_valid_after_utc:
            raise OnboardingClientError("device_credential_expired")
        context = ssl.create_default_context(cafile=str(server_ca_path) if server_ca_path is not None else None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(certificate_path), str(private_key_path))
    except OnboardingClientError:
        raise
    except (OSError, ValueError, IndexError, x509.ExtensionNotFound) as exc:
        raise OnboardingClientError("device_credentials_unavailable") from exc
    return httpx.AsyncClient(verify=context, http2=True, follow_redirects=False,
        trust_env=False, timeout=15)
