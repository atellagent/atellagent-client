"""A preconnection stall leaves time for safe recovery; accepted I/O is never replayed."""
import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import ssl
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import anyio
import httpx
import pytest
import pytest_asyncio

from atellagent_client.sdk.gateway import session as module
from connected_runtime_support import _config


@pytest.mark.parametrize('seconds', [120.0, 10.0, 2.0, .5])
def test_only_gateway_connect_wait_is_bounded(monkeypatch, seconds):
    managers = []
    def manager(**kwargs):
        managers.append(kwargs)
        return SimpleNamespace(**kwargs)
    monkeypatch.setattr(module, 'HTTPClientManager', manager)
    session = module.GatewaySession.from_service_account_config(replace(_config(), timeout=seconds))
    gateway, oauth = managers
    assert gateway['timeout'].connect == min(seconds, 2.0)
    assert gateway['timeout'].read == gateway['timeout'].write == gateway['timeout'].pool == seconds
    assert oauth['timeout'] == seconds
    assert gateway['cert'] == oauth['cert'] == (session.config.cert_path, session.config.key_path)
    assert gateway['http2'] is True
    assert callable(gateway['server_identity_validator']) and callable(oauth['server_identity_validator'])


@pytest_asyncio.fixture
async def owned_tls(tmp_path, monkeypatch):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID

    now = datetime.now(timezone.utc)
    root_key = ec.generate_private_key(ec.SECP256R1())
    root_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'owned local fixture CA')])
    root = (x509.CertificateBuilder().subject_name(root_name).issuer_name(root_name)
        .public_key(root_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
        .sign(root_key, hashes.SHA256()))
    ca_path = tmp_path/'ca.pem'
    ca_path.write_bytes(root.public_bytes(serialization.Encoding.PEM))

    def leaf(name, usage):
        key = ec.generate_private_key(ec.SECP256R1())
        builder = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
            .issuer_name(root_name).public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.ExtendedKeyUsage([usage]), False))
        if usage == ExtendedKeyUsageOID.SERVER_AUTH:
            builder = builder.add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), False)
        cert = builder.sign(root_key, hashes.SHA256())
        cp, kp = tmp_path/(name+'.pem'), tmp_path/(name+'.key')
        cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        kp.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        kp.chmod(0o600)
        return cert, cp, kp

    server_cert, server_path, server_key = leaf('server', ExtendedKeyUsageOID.SERVER_AUTH)
    _, client_path, client_key = leaf('client', ExtendedKeyUsageOID.CLIENT_AUTH)
    server_tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_tls.load_cert_chain(server_path, server_key)
    server_tls.load_verify_locations(cafile=str(ca_path))
    server_tls.verify_mode = ssl.CERT_REQUIRED
    server_tls.set_alpn_protocols(['http/1.1'])
    delivered = []
    async def handle(reader, writer):
        try:
            peer = writer.get_extra_info('peercert')
            assert peer and dict(part[0] for part in peer['subject'])['commonName'] == 'client'
            header = await reader.readuntil(b'\r\n\r\n')
            lengths = [line.split(b':', 1)[1] for line in header.split(b'\r\n') if line.lower().startswith(b'content-length:')]
            if lengths:
                await reader.readexactly(int(lengths[0]))
            delivered.append(True)
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}')
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle, '127.0.0.1', 0, ssl=server_tls)
    origin = 'https://127.0.0.1:'+str(server.sockets[0].getsockname()[1])
    expected = hashlib.sha256(server_cert.public_bytes(serialization.Encoding.DER)).digest()
    pinned = []
    def pin(ssl_object):
        good = bool(ssl_object) and hashlib.sha256(ssl_object.getpeercert(binary_form=True)).digest() == expected
        pinned.append(good)
        return good
    monkeypatch.setenv('SSL_CERT_FILE', str(ca_path))
    monkeypatch.setattr(module, 'build_gateway_cert_validator', lambda _url: pin)
    config = replace(_config(), gateway_url=origin, oauth_token_url=origin+'/token',
        oauth_jwks_url=origin+'/jwks', cert_path=str(client_path), key_path=str(client_key), timeout=10.0)
    session = module.GatewaySession.from_service_account_config(config)
    session._auth_headers_async = AsyncMock(return_value={'X-Owned-Fixture': '1'})
    try:
        yield SimpleNamespace(session=session, url=origin+'/effect', delivered=delivered, pinned=pinned)
    finally:
        await session.close_async()
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_actual_preconnect_stall_recovers_over_verified_mtls_once(owned_tls, monkeypatch):
    original = anyio.connect_tcp
    attempts, cancelled = [], []
    async def connect(*args, **kwargs):
        attempts.append(True)
        if len(attempts) == 1:
            try:
                await asyncio.sleep(30)
            finally:
                cancelled.append(True)
        return await original(*args, **kwargs)
    monkeypatch.setattr(anyio, 'connect_tcp', connect)
    started = time.monotonic()
    response = await asyncio.wait_for(owned_tls.session.request_authenticated('POST', owned_tls.url, json={'owned': True}), 3.5)
    elapsed = time.monotonic()-started
    assert response.status_code == 200 and 1.8 <= elapsed < 3.5
    assert len(attempts) == 2 and cancelled == [True]
    assert owned_tls.delivered == [True] and owned_tls.pinned == [True]
    assert owned_tls.session._auth_headers_async.await_count == 2


@pytest.mark.asyncio
async def test_two_stalled_preconnects_are_finite_and_never_deliver(owned_tls, monkeypatch):
    attempts = []
    async def connect(*args, **kwargs):
        attempts.append(True)
        await asyncio.sleep(30)
    monkeypatch.setattr(anyio, 'connect_tcp', connect)
    started = time.monotonic()
    with pytest.raises(httpx.ConnectTimeout):
        await asyncio.wait_for(owned_tls.session.request_authenticated('POST', owned_tls.url, json={'owned': True}), 5)
    assert 3.6 <= time.monotonic()-started < 5
    assert len(attempts) == 2 and owned_tls.delivered == [] and owned_tls.pinned == []


@pytest.mark.asyncio
async def test_caller_cancellation_does_not_trigger_connect_retry(owned_tls, monkeypatch):
    entered = asyncio.Event()
    attempts = []
    async def connect(*args, **kwargs):
        attempts.append(True)
        entered.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(anyio, 'connect_tcp', connect)
    task = asyncio.create_task(owned_tls.session.request_authenticated('POST', owned_tls.url))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(attempts) == 1 and owned_tls.delivered == []
