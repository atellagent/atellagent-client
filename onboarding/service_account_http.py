# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

"""Dedicated per-integration mTLS pool; never device or browser credentials."""

from datetime import datetime, timezone
from hashlib import sha256
import ssl

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.x509.oid import ExtendedKeyUsageOID
import httpx

from .atomic_private_file import read_private_file
from .certificate_material import parse_certificates
from .errors import OnboardingClientError


def create_service_account_http_client(*, tenant_id, service_account_id, certificate_path,
        private_key_path, certificate_sha256, server_ca_path=None):
    try:
        if certificate_path == private_key_path:
            raise ValueError("distinct credential paths required")
        material = parse_certificates(read_private_file(certificate_path, limit=65536).decode("ascii"), maximum=9)
        read_private_file(private_key_path, limit=8192)
        leaf = material[0]
        expected = f"sa.{service_account_id.hex}.{tenant_id.hex}.identity.invalid"
        constraints = leaf.extensions.get_extension_for_class(x509.BasicConstraints).value
        if (list(leaf.extensions.get_extension_for_class(x509.SubjectAlternativeName).value) != [x509.DNSName(expected)]
                or list(leaf.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value) != [ExtendedKeyUsageOID.CLIENT_AUTH]
                or constraints.ca or constraints.path_length is not None
                or sha256(leaf.public_bytes(serialization.Encoding.DER)).hexdigest() != certificate_sha256):
            raise ValueError("exact service-account client leaf required")
        if not leaf.not_valid_before_utc <= datetime.now(timezone.utc) < leaf.not_valid_after_utc:
            raise OnboardingClientError("service_account_credential_not_current")
        # The supplied client chain never becomes TLS server trust.
        context = ssl.create_default_context(cafile=str(server_ca_path) if server_ca_path is not None else None)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(str(certificate_path), str(private_key_path))
    except OnboardingClientError:
        raise
    except (OSError, ValueError, TypeError, AttributeError, x509.ExtensionNotFound, x509.DuplicateExtension) as exc:
        raise OnboardingClientError("service_account_credentials_unavailable") from exc
    return httpx.AsyncClient(verify=context, http2=True, follow_redirects=False, trust_env=False, timeout=15)
