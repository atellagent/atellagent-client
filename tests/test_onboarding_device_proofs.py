"""Device proof wire binding, independent of native controllers and key storage."""

import base64
from hashlib import sha256
from uuid import uuid4

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest

from atellagent_client.onboarding.proofs import sign_device_request, canonical_json


@pytest.mark.parametrize("method,body", [("GET", None), ("PUT", {"device_name": "Chris’s Mac"})])
def test_signature_binds_device_method_target_body_and_fresh_nonce(method, body):
    key, device = Ed25519PrivateKey.generate(), uuid4()
    arguments = dict(private_key=key, device_id=device, method=method,
        url="https://device.example/devices/self/name?revision=1", body=body)
    headers = sign_device_request(**arguments)
    signature = base64.urlsafe_b64decode(headers["X-Atellagent-Device-Signature"] + "==")
    digest = sha256(b"" if body is None else '{"device_name":"Chris’s Mac"}'.encode()).hexdigest()
    material = "\n".join(("atellagent-device-request-v1", str(device), method,
        "/devices/self/name?revision=1", digest, headers["X-Atellagent-Device-Timestamp"],
        headers["X-Atellagent-Device-Nonce"])).encode()
    key.public_key().verify(signature, material)
    assert headers["X-Atellagent-Device-Id"] == str(device)
    assert sign_device_request(**arguments)["X-Atellagent-Device-Nonce"] != headers["X-Atellagent-Device-Nonce"]
    with pytest.raises(InvalidSignature):
        key.public_key().verify(signature, material.replace(b"revision=1", b"revision=2"))


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_json_numbers_cannot_be_signed(value):
    with pytest.raises(ValueError):
        canonical_json({"value": value})


@pytest.mark.parametrize("method,url", [("GET", "relative/path"),
    ("GET", "https://device.example/path#fragment"), ("GET\nPOST", "/path")])
def test_ambiguous_request_target_is_rejected(method, url):
    with pytest.raises(ValueError):
        sign_device_request(private_key=Ed25519PrivateKey.generate(), device_id=uuid4(),
            method=method, url=url)
