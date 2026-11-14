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
    server_tls.set_alpn_protocols(['h2'])
    delivered = []
    connections=[]
    async def handle(reader, writer):
        from h2.connection import H2Connection
        from h2.config import H2Configuration
        from h2.events import RequestReceived,DataReceived,StreamEnded
        certificate=writer.get_extra_info('ssl_object').getpeercert(binary_form=True)
        connections.append(hashlib.sha256(certificate).hexdigest())
        connection=H2Connection(config=H2Configuration(client_side=False,header_encoding='utf-8'))
        requests={}
        try:
            connection.initiate_connection();writer.write(connection.data_to_send());await writer.drain()
            while data:=await reader.read(65536):
                for event in connection.receive_data(data):
                    if isinstance(event,RequestReceived):requests[event.stream_id]=dict(event.headers)
                    elif isinstance(event,DataReceived):connection.acknowledge_received_data(event.flow_controlled_length,event.stream_id)
                    elif isinstance(event,StreamEnded):
                        request=requests.pop(event.stream_id);delivered.append(request)
                        if request[':path']=='/lost-reply':return
                        connection.send_headers(event.stream_id,[(':status','200'),('content-length','2')])
                        connection.send_data(event.stream_id,b'{}',end_stream=True)
                        if request[':path']=='/goaway':connection.close_connection()
                writer.write(connection.data_to_send());await writer.drain()
        except (ConnectionError,asyncio.IncompleteReadError):pass
        finally:
            writer.close()
            try:await writer.wait_closed()
            except ConnectionError:pass

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
        yield SimpleNamespace(session=session, url=origin+'/effect', origin=origin, delivered=delivered, pinned=pinned,connections=connections,reissue=lambda:leaf('client',ExtendedKeyUsageOID.CLIENT_AUTH),server_cert=str(server_path),server_key=str(server_key))
    finally:
        await session.close_async()
        server.close()
        await server.wait_closed()



@pytest.mark.asyncio
async def test_actual_mtls_http2_reuse_preserves_per_request_auth_and_pin(owned_tls):
    s=owned_tls.session
    s._auth_headers_async=AsyncMock(side_effect=[{'X-Owned-Fixture':'first'},{'X-Owned-Fixture':'second'}])
    for _ in range(2):
        r=await s.request_protocol_authenticated('POST',owned_tls.url,json={'synthetic':True})
        assert r.http_version=='HTTP/2'and r.status_code==200
    assert len(owned_tls.connections)==1 and owned_tls.pinned==[True,True]
    assert [x['x-owned-fixture']for x in owned_tls.delivered]==['first','second']

@pytest.mark.asyncio
async def test_certificate_rotation_reloads_mtls_and_closes_idle_connection(owned_tls):
    s=owned_tls.session
    await s.request_protocol_authenticated('POST',owned_tls.url)
    owned_tls.reissue()
    await s.request_protocol_authenticated('POST',owned_tls.url)
    assert len(owned_tls.connections)==2 and owned_tls.connections[0]!=owned_tls.connections[1]
    assert owned_tls.pinned==[True,True]

@pytest.mark.asyncio
async def test_untrusted_client_certificate_is_never_delivered(owned_tls):
    s=owned_tls.session;s.http_client_manager._cert_tuple=(owned_tls.server_cert,owned_tls.server_key)
    with pytest.raises((httpx.TransportError,ssl.SSLError)):
        await s.request_protocol_authenticated('POST',owned_tls.url)
    assert not owned_tls.delivered

@pytest.mark.asyncio
async def test_missing_key_fails_closed_even_with_an_idle_authenticated_connection(owned_tls):
    from pathlib import Path
    s=owned_tls.session
    await s.request_protocol_authenticated('POST',owned_tls.url)
    Path(s.config.key_path).unlink()
    with pytest.raises(OSError):await s.request_protocol_authenticated('POST',owned_tls.url)
    assert len(owned_tls.delivered)==1

@pytest.mark.asyncio
async def test_reused_connection_still_rejects_server_pin_failure(owned_tls):
    s=owned_tls.session
    await s.request_protocol_authenticated('POST',owned_tls.url)
    s.http_client_manager._server_identity_validator=lambda _:False
    with pytest.raises(RuntimeError,match='certificate verification failed'):
        await s.request_protocol_authenticated('POST',owned_tls.url)
    assert len(owned_tls.connections)==1 and len(owned_tls.delivered)==2
    assert not s.http_client_manager._request_pool._idle

@pytest.mark.asyncio
async def test_lost_reply_is_not_replayed_and_only_its_connection_is_discarded(owned_tls):
    s=owned_tls.session
    with pytest.raises(httpx.TransportError):await s.request_protocol_authenticated('POST',owned_tls.origin+'/lost-reply')
    assert len(owned_tls.delivered)==1
    await s.request_protocol_authenticated('POST',owned_tls.url)
    assert len(owned_tls.delivered)==2 and len(owned_tls.connections)==2

@pytest.mark.asyncio
async def test_http2_goaway_never_duplicates_an_accepted_post(owned_tls):
    s=owned_tls.session
    await s.request_protocol_authenticated('POST',owned_tls.origin+'/goaway')
    try:await s.request_protocol_authenticated('POST',owned_tls.url)
    except httpx.TransportError:
        assert len(owned_tls.delivered)==1
        await s.request_protocol_authenticated('POST',owned_tls.url)
    assert len(owned_tls.delivered)==2
