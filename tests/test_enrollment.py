# Copyright (c) 2026 Atellagent, Inc. All rights reserved.
# This source code is licensed under the terms found in the LICENSE.md file in the root directory of this source tree.

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from unittest.mock import AsyncMock
from uuid import UUID

import httpx
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from atellagent_client.sdk.config import load_service_account_config_from_yaml
from atellagent_client.sdk.enrollment import (
    CertificateEnrollmentError,
    enroll_service_account_certificate,
)

TENANT_ID = "00000000-0000-0000-0000-000000000002"
SERVICE_ACCOUNT_ID = "00000000-0000-0000-0000-000000000001"
ENROLLMENT_TOKEN = "enr_00000000-0000-0000-0000-000000000003." + ("a" * 43)


def _root_certificate() -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test Root")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, certificate


def _issue_for_csr(
    csr: x509.CertificateSigningRequest,
    root_key: rsa.RSAPrivateKey,
    root_certificate: x509.Certificate,
    *,
    public_key=None,
) -> x509.Certificate:
    now = datetime.now(timezone.utc)
    return (
        x509.CertificateBuilder()
        .subject_name(csr.subject)
        .issuer_name(root_certificate.subject)
        .public_key(public_key or csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(
            csr.extensions.get_extension_for_class(x509.SubjectAlternativeName).value,
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(root_key, hashes.SHA256())
    )


class CertificateEnrollmentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tempdir.cleanup)
        self.root = Path(self.tempdir.name)
        self.config_path = self.root / "service-account.yaml"
        self.config_path.write_text(
            yaml.safe_dump(
                {
                    "gateway_mtls_url": "https://mtls.gateway.example",
                    "oauth_token_url": "https://mtls.auth.example/token",
                    "oauth_jwks_url": "https://mtls.auth.example/jwks",
                    "client_id": "client-id",
                    "service_account_id": SERVICE_ACCOUNT_ID,
                    "integration_id": "00000000-0000-0000-0000-000000000004",
                    "tenant_id": TENANT_ID,
                    "placement": "connected",
                    "protocol_version": "v1",
                    "certificate_enrollment_url": "https://atellagent.example/enroll",
                    "certificate_enrollment_expires_at": (
                        datetime.now(timezone.utc) + timedelta(minutes=15)
                    ).isoformat(),
                    "integration_type": "agent",
                    "identity_mode": "boundary_identity_only",
                    "deployment": {"type": "sdk"},
                }
            ),
            encoding="utf-8",
        )

    async def test_generates_bound_csr_and_persists_only_local_key(self) -> None:
        root_key, root_certificate = _root_certificate()
        observed_csrs: list[bytes] = []

        def handler(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content)
            self.assertEqual(payload["token"], ENROLLMENT_TOKEN)
            self.assertNotIn("private_key", payload)
            observed_csrs.append(payload["csr_pem"].encode("ascii"))
            if len(observed_csrs) == 1:
                return httpx.Response(
                    202,
                    json={
                        "success": True,
                        "operation_id": "operation-1",
                        "status": "pending",
                    },
                )
            csr = x509.load_pem_x509_csr(observed_csrs[-1])
            self.assertTrue(csr.is_signature_valid)
            self.assertIsInstance(csr.public_key(), rsa.RSAPublicKey)
            self.assertEqual(csr.public_key().key_size, 2048)
            public_key_der = csr.public_key().public_bytes(
                serialization.Encoding.DER,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            digest = hashes.Hash(hashes.SHA256())
            digest.update(public_key_der)
            expected_cn = f"sa:{SERVICE_ACCOUNT_ID}:{digest.finalize().hex()[:16]}"
            self.assertEqual(
                csr.subject,
                x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, expected_cn)]),
            )
            san = csr.extensions.get_extension_for_class(
                x509.SubjectAlternativeName
            ).value
            self.assertEqual(len(san), 1)
            self.assertEqual(
                san.get_values_for_type(x509.DNSName),
                [
                    f"sa.{UUID(SERVICE_ACCOUNT_ID).hex}."
                    f"{UUID(TENANT_ID).hex}.identity.invalid"
                ],
            )
            self.assertEqual(
                set(
                    csr.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
                ),
                {ExtendedKeyUsageOID.CLIENT_AUTH},
            )
            leaf = _issue_for_csr(csr, root_key, root_certificate)
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "operation_id": "operation-1",
                    "status": "succeeded",
                    "certificate": {
                        "certificate_pem": leaf.public_bytes(
                            serialization.Encoding.PEM
                        ).decode("ascii"),
                        "certificate_chain_pem": root_certificate.public_bytes(
                            serialization.Encoding.PEM
                        ).decode("ascii"),
                    },
                },
            )

        with patch(
            "atellagent_client.sdk.enrollment.asyncio.sleep",
            new=AsyncMock(),
        ):
            result = await enroll_service_account_certificate(
                config_path=self.config_path,
                enrollment_token=ENROLLMENT_TOKEN,
                transport=httpx.MockTransport(handler),
            )

        self.assertEqual(len(observed_csrs), 2)
        self.assertEqual(observed_csrs[0], observed_csrs[1])
        self.assertEqual(result.operation_id, "operation-1")
        self.assertEqual(
            stat.S_IMODE(result.private_key_path.stat().st_mode),
            0o600,
        )
        self.assertEqual(
            stat.S_IMODE(result.certificate_path.stat().st_mode),
            0o644,
        )
        self.assertEqual(
            result.certificate_path.read_text(encoding="ascii").count(
                "-----BEGIN CERTIFICATE-----"
            ),
            2,
        )
        self.assertEqual(
            sorted(path.name for path in (self.root / "certs").iterdir()),
            ["client-cert.pem", "client-key.pem"],
        )
        local_key = serialization.load_pem_private_key(
            result.private_key_path.read_bytes(), password=None
        )
        leaf = x509.load_pem_x509_certificate(result.certificate_path.read_bytes())
        self.assertEqual(
            local_key.public_key().public_numbers(),
            leaf.public_key().public_numbers(),
        )

    async def test_rejects_certificate_for_another_key_without_writing_files(
        self,
    ) -> None:
        root_key, root_certificate = _root_certificate()
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

        def handler(request: httpx.Request) -> httpx.Response:
            csr = x509.load_pem_x509_csr(
                json.loads(request.content)["csr_pem"].encode()
            )
            leaf = _issue_for_csr(
                csr,
                root_key,
                root_certificate,
                public_key=other_key.public_key(),
            )
            return httpx.Response(
                200,
                json={
                    "operation_id": "operation-2",
                    "certificate": {
                        "certificate_pem": leaf.public_bytes(
                            serialization.Encoding.PEM
                        ).decode(),
                        "certificate_chain_pem": root_certificate.public_bytes(
                            serialization.Encoding.PEM
                        ).decode(),
                    },
                },
            )

        with self.assertRaisesRegex(CertificateEnrollmentError, "local private key"):
            await enroll_service_account_certificate(
                config_path=self.config_path,
                enrollment_token=ENROLLMENT_TOKEN,
                transport=httpx.MockTransport(handler),
            )
        self.assertFalse((self.root / "certs" / "client-cert.pem").exists())
        self.assertFalse((self.root / "certs" / "client-key.pem").exists())

    def test_runtime_loads_environment_backed_client_credentials(self) -> None:
        env = {
            "ATELLAGENT_CERT_PATH": "/credentials/client-cert.pem",
            "ATELLAGENT_KEY_PATH": "/credentials/client-key.pem",
        }
        with patch.dict(os.environ, env, clear=True):
            config = load_service_account_config_from_yaml(str(self.config_path))
            self.assertEqual(config.cert_path, env["ATELLAGENT_CERT_PATH"])
            self.assertEqual(config.key_path, env["ATELLAGENT_KEY_PATH"])

    def test_runtime_resolves_generated_relative_credential_paths_beside_yaml(self) -> None:
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        data["client_certificate_path"] = "./certs/client-cert.pem"
        data["client_private_key_path"] = "./certs/client-key.pem"
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")

        with patch.dict(os.environ, {}, clear=True):
            config = load_service_account_config_from_yaml(str(self.config_path))

        self.assertEqual(
            config.cert_path,
            str((self.root / "certs" / "client-cert.pem").resolve()),
        )
        self.assertEqual(
            config.key_path,
            str((self.root / "certs" / "client-key.pem").resolve()),
        )

    def test_connected_config_loads_matching_packaging(self) -> None:
        with patch.dict(
            os.environ,
            {
                "ATELLAGENT_CERT_PATH": "/credentials/client-cert.pem",
                "ATELLAGENT_KEY_PATH": "/credentials/client-key.pem",
            },
            clear=True,
        ):
            config = load_service_account_config_from_yaml(str(self.config_path))
        self.assertEqual(config.packaging, "sdk")
        self.assertEqual(config.deployment.type, "sdk")

    def test_local_control_configuration_is_mcp_only_and_resolves_relative_manifest(self) -> None:
        data = yaml.safe_load(self.config_path.read_text(encoding="utf-8"))
        data.update(
            {
                "integration_type": "mcp",
                "identity_mode": "boundary_identity_only",
                "packaging": "bridge",
                "deployment": {
                    "type": "bridge",
                    "target_transport": "handler",
                },
                "mcp_descriptor_path_template": (
                    "/v1/connected-runtimes/instances/{instance_id}/descriptors/mcp"
                ),
                "control_source": "local_manifest",
                "local_guardrail_manifest_path": "./local-guardrails.yaml",
                "local_guardrail_mode": "enforce",
            }
        )
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        (self.root / "local-guardrails.yaml").write_text(
            "schema_version: v1\nmode: enforce\nactions:\n  file.read:\n    readable_roots: [/workspace]\n",
            encoding="utf-8",
        )
        with patch.dict(
            os.environ,
            {
                "ATELLAGENT_CERT_PATH": "/credentials/client-cert.pem",
                "ATELLAGENT_KEY_PATH": "/credentials/client-key.pem",
            },
            clear=True,
        ):
            config = load_service_account_config_from_yaml(str(self.config_path))
        self.assertEqual(config.control_source, "local_manifest")
        self.assertEqual(config.local_guardrail_mode, "enforce")
        self.assertEqual(
            config.local_guardrail_manifest_path,
            str((self.root / "local-guardrails.yaml").resolve()),
        )

        data["integration_type"] = "agent"
        self.config_path.write_text(yaml.safe_dump(data), encoding="utf-8")
        with patch.dict(
            os.environ,
            {
                "ATELLAGENT_CERT_PATH": "/credentials/client-cert.pem",
                "ATELLAGENT_KEY_PATH": "/credentials/client-key.pem",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "only for connected MCP"):
                load_service_account_config_from_yaml(str(self.config_path))


if __name__ == "__main__":
    unittest.main()
