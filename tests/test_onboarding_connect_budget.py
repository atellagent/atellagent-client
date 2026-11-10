"""Actual TLS and send/read boundaries for bounded onboarding establishment."""
import asyncio
import ssl
import time

import anyio
import httpx
import pytest

from atellagent_client.onboarding.http_response import request_json
from atellagent_client.onboarding.errors import OnboardingClientError
from test_gateway_connect_budget import owned_tls

HEADERS={'X-Atellagent-Onboarding-Nonce':'exact-original-proof'}


@pytest.mark.asyncio
@pytest.mark.parametrize('connect',[None,.1,2,15])
async def test_only_connect_timeout_is_capped_and_signed_input_is_unchanged(connect):
    calls=[]
    def send(request):
        calls.append(request)
        return httpx.Response(200,json={'ready':True})
    async with httpx.AsyncClient(transport=httpx.MockTransport(send),timeout=httpx.Timeout(17,connect=connect))as client:
        assert await request_json(client,method='POST',url='https://fixture/activate',headers=HEADERS,
                                  content=b'exact-body',error_prefix='onboarding')=={'ready':True}
    assert len(calls)==1 and calls[0].content==b'exact-body'
    assert calls[0].headers['X-Atellagent-Onboarding-Nonce']=='exact-original-proof'
    assert calls[0].extensions['timeout']=={'connect':min(connect,2)if connect is not None else 2,'read':17,'write':17,'pool':17}


@pytest.mark.asyncio
@pytest.mark.parametrize('failure',[httpx.ReadTimeout,httpx.WriteTimeout,httpx.ReadError,httpx.WriteError,httpx.RemoteProtocolError])
async def test_post_send_or_response_uncertainty_is_never_retried(failure):
    calls=[]
    def send(request):
        calls.append(request)
        raise failure('private details',request=request)
    async with httpx.AsyncClient(transport=httpx.MockTransport(send))as client:
        with pytest.raises(OnboardingClientError)as error:
            await request_json(client,method='POST',url='https://fixture/activate',headers=HEADERS,
                               content=b'exact-body',error_prefix='onboarding')
    assert len(calls)==1 and 'private'not in str(error.value)


def tls_client(fixture,*,trusted=True):
    context=ssl.create_default_context()if trusted else ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.load_cert_chain(fixture.session.config.cert_path,fixture.session.config.key_path)
    return httpx.AsyncClient(verify=context,http2=True,trust_env=False,timeout=10)


@pytest.mark.asyncio
async def test_actual_onboarding_preconnect_stall_delivers_one_mtls_post(owned_tls,monkeypatch):
    original=anyio.connect_tcp;attempts=[];cancelled=[]
    async def connect(*args,**kwargs):
        attempts.append(True)
        if len(attempts)==1:
            try:await asyncio.sleep(30)
            finally:cancelled.append(True)
        return await original(*args,**kwargs)
    monkeypatch.setattr(anyio,'connect_tcp',connect)
    started=time.monotonic()
    async with tls_client(owned_tls)as client:
        assert await asyncio.wait_for(request_json(client,method='POST',url=owned_tls.url,
            headers=HEADERS,content=b'exact-activation',error_prefix='onboarding'),3.7)=={}
    assert 1.8<=time.monotonic()-started<3.7
    assert len(attempts)==2 and cancelled==[True]and owned_tls.delivered==[True]


@pytest.mark.asyncio
async def test_two_stalled_onboarding_connections_end_without_any_http_delivery(owned_tls,monkeypatch):
    attempts=[]
    async def connect(*args,**kwargs):
        attempts.append(True);await asyncio.sleep(30)
    monkeypatch.setattr(anyio,'connect_tcp',connect);started=time.monotonic()
    async with tls_client(owned_tls)as client:
        with pytest.raises(OnboardingClientError):
            await asyncio.wait_for(request_json(client,method='POST',url=owned_tls.url,
                headers=HEADERS,content=b'exact-activation',error_prefix='onboarding'),5)
    assert 3.8<=time.monotonic()-started<5
    assert len(attempts)==2 and owned_tls.delivered==[]


@pytest.mark.asyncio
async def test_actual_onboarding_cancellation_never_opens_a_replacement_connection(owned_tls,monkeypatch):
    entered=asyncio.Event();attempts=[]
    async def connect(*args,**kwargs):
        attempts.append(True);entered.set();await asyncio.Event().wait()
    monkeypatch.setattr(anyio,'connect_tcp',connect)
    async with tls_client(owned_tls)as client:
        task=asyncio.create_task(request_json(client,method='POST',url=owned_tls.url,
            headers=HEADERS,content=b'exact-activation',error_prefix='onboarding'))
        await asyncio.wait_for(entered.wait(),1);task.cancel()
        with pytest.raises(asyncio.CancelledError):await task
    assert len(attempts)==1 and owned_tls.delivered==[]


@pytest.mark.asyncio
async def test_actual_tls_rejection_never_retries_or_delivers_http(owned_tls,monkeypatch):
    original=anyio.connect_tcp;attempts=[]
    async def connect(*args,**kwargs):
        attempts.append(True);return await original(*args,**kwargs)
    monkeypatch.setattr(anyio,'connect_tcp',connect)
    async with tls_client(owned_tls,trusted=False)as client:
        with pytest.raises(OnboardingClientError):
            await request_json(client,method='POST',url=owned_tls.url,
                headers=HEADERS,content=b'exact-activation',error_prefix='onboarding')
    assert len(attempts)==1 and owned_tls.delivered==[]
