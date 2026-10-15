"""Device pools reject agent credentials and unsafe local private-key storage."""

import os
import ssl
from uuid import uuid4

import pytest

from atellagent_client.onboarding.device_http import create_device_http_client
from atellagent_client.onboarding import device_http
from atellagent_client.onboarding.errors import OnboardingClientError
from onboarding_support import identity, credential


@pytest.mark.asyncio
async def test_device_pool_has_verified_tls_no_redirects_or_environment_proxy(tmp_path, monkeypatch):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    credential(device)
    created, factory = {}, device_http.httpx.AsyncClient
    def construct(**kwargs):
        created.update(kwargs)
        return factory(**kwargs)
    monkeypatch.setattr(device_http.httpx, "AsyncClient", construct)
    client = create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
        certificate_path=device.certificate_path, private_key_path=device.private_key_path)
    try:
        assert not client.follow_redirects and not client.trust_env
        context = created["verify"]
        assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    finally:
        await client.aclose()


def test_service_account_certificate_cannot_open_device_pool(tmp_path):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    credential(device, kind="sa")
    with pytest.raises(OnboardingClientError) as error:
        create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
            certificate_path=device.certificate_path, private_key_path=device.private_key_path)
    assert error.value.code == "device_credentials_unavailable"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permission/reparse counterpart needs Windows acceptance")
@pytest.mark.parametrize("unsafe", ["key_mode", "directory_mode", "symlink"])
def test_unsafe_paths_are_rejected_without_permission_repair(tmp_path, unsafe):
    device = identity(tmp_path / "device", tenant=uuid4(), device=uuid4(), owner=uuid4())
    credential(device)
    if unsafe == "key_mode":
        os.chmod(device.private_key_path, 0o644)
    elif unsafe == "directory_mode":
        os.chmod(device.private_key_path.parent, 0o755)
    else:
        device.private_key_path.rename(device.private_key_path.with_suffix(".original"))
        device.private_key_path.symlink_to(device.private_key_path.with_suffix(".original"))
    with pytest.raises(OnboardingClientError):
        create_device_http_client(tenant_id=device.tenant_id, device_id=device.device_id,
            certificate_path=device.certificate_path, private_key_path=device.private_key_path)
    if unsafe == "key_mode":
        assert device.private_key_path.stat().st_mode & 0o777 == 0o644
