"""Reuse key parsing without reusing file permissions or certificate authority."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from atellagent_client.sdk import private_keys as module
from atellagent_client.onboarding.atomic_private_file import read_private_file


@pytest.fixture(autouse=True)
def empty_cache():
    with module._lock:
        module._keys.clear()
    yield
    with module._lock:
        module._keys.clear()


def encoded(key):
    return key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())


def test_identical_bytes_are_validated_once_but_replacement_is_validated(monkeypatch):
    original = serialization.load_pem_private_key
    parse = Mock(wraps=original)
    monkeypatch.setattr(module.serialization, "load_pem_private_key", parse)
    first, second = (encoded(rsa.generate_private_key(public_exponent=65537, key_size=2048))
                     for _ in range(2))
    key = module.load_private_key(first)
    assert all(module.load_private_key(first) is key for _ in range(66))
    replacement = module.load_private_key(second)
    assert replacement.public_key().public_numbers() != key.public_key().public_numbers()
    assert parse.call_count == 2
    assert parse.call_args_list[0].kwargs == {"password": None}


def test_invalid_material_is_never_cached(monkeypatch):
    parse = Mock(side_effect=ValueError("invalid key"))
    monkeypatch.setattr(module.serialization, "load_pem_private_key", parse)
    for _ in range(2):
        with pytest.raises(ValueError):
            module.load_private_key(b"invalid")
    assert parse.call_count == 2 and not module._keys
    for raw in ("not bytes", b"", b"x" * 8193):
        with pytest.raises(ValueError):
            module.load_private_key(raw)
    assert parse.call_count == 2


def test_concurrent_first_reads_share_validation(monkeypatch):
    entered, release = Event(), Event()
    key = object()
    def parse(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return key
    parsing = Mock(side_effect=parse)
    monkeypatch.setattr(module.serialization, "load_pem_private_key", parsing)
    with ThreadPoolExecutor(max_workers=4) as workers:
        first = workers.submit(module.load_private_key, b"same bytes")
        assert entered.wait(5)
        others = [workers.submit(module.load_private_key, b"same bytes") for _ in range(3)]
        release.set()
        assert all(item.result(5) is key for item in [first, *others])
    assert parsing.call_count == 1


def test_cache_is_bounded_and_keeps_recently_used_keys(monkeypatch):
    parse = Mock(side_effect=lambda *args, **kwargs: object())
    monkeypatch.setattr(module.serialization, "load_pem_private_key", parse)
    for index in range(module._LIMIT):
        module.load_private_key(str(index).encode())
    recent = module.load_private_key(b"0")
    module.load_private_key(b"new")
    assert len(module._keys) == module._LIMIT
    assert module.load_private_key(b"0") is recent
    assert b"1" not in module._keys
    module.load_private_key(b"1")
    assert parse.call_count == module._LIMIT + 2


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_cached_key_does_not_bypass_private_file_checks(tmp_path):
    directory = tmp_path / "owned"
    directory.mkdir(mode=0o700)
    path = directory / "key.pem"
    path.write_bytes(encoded(rsa.generate_private_key(public_exponent=65537, key_size=2048)))
    path.chmod(0o600)
    module.load_private_key(read_private_file(path, limit=8192))
    path.chmod(0o644)
    with pytest.raises(ValueError):
        module.load_private_key(read_private_file(path, limit=8192))


@pytest.mark.asyncio
async def test_device_context_cold_read_does_not_block_network_loop(monkeypatch, tmp_path):
    from atellagent_client.onboarding import device_context as context
    entered, release = Event(), Event()
    def loading(_self):
        entered.set()
        assert release.wait(5)
        raise ValueError("test read finished")
    monkeypatch.setattr(context.DeviceInstallation, "load", loading)
    opening = asyncio.create_task(context.DeviceContext.open(tmp_path / "installation"))
    try:
        # The worker is held until the event loop demonstrates progress.
        for _ in range(500):
            if entered.is_set():
                break
            await asyncio.sleep(0.001)
        assert entered.is_set() and not opening.done()
    finally:
        release.set()
        with pytest.raises(ValueError, match="test read finished"):
            await opening
