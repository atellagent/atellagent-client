"""Test-only admitted device and certificate material; no platform enrollment."""

from datetime import datetime, timedelta, timezone
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa, ed25519
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from atellagent_client.onboarding.device_identity import AdmittedDevice


def identity(directory, *, tenant, device, owner):
    directory.mkdir(mode=0o700)
    return AdmittedDevice(tenant, device, owner, ed25519.Ed25519PrivateKey.generate(),
        directory / "certificate.pem", directory / "key.pem")


def credential(device, *, kind="device"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.now(timezone.utc)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "test-device")])
    san = f"{kind}.{device.device_id.hex}.{device.tenant_id.hex}.identity.invalid"
    leaf = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(san)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256()))
    device.certificate_path.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    device.private_key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    os.chmod(device.private_key_path, 0o600)
    return leaf


def metadata(device):
    now = datetime.now(timezone.utc)
    return dict(device_id=str(device.device_id), tenant_id=str(device.tenant_id),
        owner_user_id=str(device.owner_user_id), tenant_name="Test organization", device_name="My laptop", status="active",
        discovery_reporting_status="enabled", discovery_fresh=False, discovery_observed_at=None,
        certificate_expires_at=(now + timedelta(days=1)).isoformat(), server_time=now.isoformat())
