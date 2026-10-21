"""Transport fallback never supplies missing provenance or overrides access policy."""
import asyncio
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from calendar_evidence_acquisition.nse_browser import NSEBrowser
from calendar_evidence_acquisition.nse_discovery import Failure, TITLE

URL = 'https://nsearchives.nseindia.com/observed-example.zip'
SELECTED = dict(download_url=URL, report_name=TITLE, trading_date='2026-10-01', method='DOM_LINK')


def test_http2_error_switches_once_to_http1_preserving_request():
    b = NSEBrowser()
    calls = []
    async def observe(day):
        calls.append((day, b.http1))
        if not b.http1:
            raise Failure('NSE_CONNECTION_FAILED', detail='ERR_HTTP2_PROTOCOL_ERROR', transient=True)
        return {'records': [SELECTED]}
    b._observe = observe
    try:
        assert b.observe(date(2026, 10, 1))['records'] == [SELECTED]
        assert calls == [(date(2026, 10, 1), False), (date(2026, 10, 1), True)]
    finally:
        b.close()


@pytest.mark.parametrize('reason,detail', [
    ('NSE_HTTP_403', None), ('NSE_HTTP_429', None), ('NSE_ROBOTS_DISALLOWED', None),
    ('NSE_REDIRECT_REQUIRES_REVIEW', None), ('SOURCE_METADATA_REQUIRED', None),
    ('NSE_CONNECTION_FAILED', 'ERR_CERT_AUTHORITY_INVALID'),
])
def test_access_provenance_and_certificate_failures_never_switch_transport(reason, detail):
    b = NSEBrowser()
    b._observe = AsyncMock(side_effect=Failure(reason, detail=detail))
    try:
        with pytest.raises(Failure, match=reason): b.observe(None)
        assert b._observe.await_count == 1
        assert not b.http1
    finally: b.close()


def test_http1_exhaustion_is_terminal_not_three_more_acquisition_retries():
    b = NSEBrowser()
    b._observe = AsyncMock(side_effect=[
        Failure('NSE_CONNECTION_FAILED', detail='ERR_HTTP2_PROTOCOL_ERROR'),
        Failure('NSE_TIMEOUT', transient=True),
    ])
    try:
        with pytest.raises(Failure) as result: b.observe(None)
        assert result.value.reason == 'NSE_HTTP1_TRANSPORT_FAILED'
        assert not result.value.transient
        assert b._observe.await_count == 2
    finally: b.close()


def test_direct_fallback_requires_exact_report_date_url_and_policy(tmp_path, monkeypatch):
    from calendar_evidence_acquisition import nse_transport
    b = NSEBrowser()
    b.records = [SELECTED]
    b.policy = AsyncMock()
    b._download = AsyncMock(side_effect=Failure('NSE_DOWNLOAD_FAILED', detail='ERR_HTTP2_PROTOCOL_ERROR'))
    calls = []
    monkeypatch.setattr(nse_transport, 'download_http1', lambda url, path: calls.append((url, path)) or {'url': url})
    try:
        assert b.download(SELECTED, tmp_path/'pending.zip') == {'url': URL}
        b.policy.assert_awaited_once_with(URL)
        assert calls == [(URL, tmp_path/'pending.zip')]
    finally: b.close()


@pytest.mark.parametrize('mutation', [{'report_name':'Other report'}, {'trading_date':'2026-10-02'}, {'download_url':'https://evil.test/a.zip'}])
def test_direct_fallback_cannot_promote_unobserved_selection(tmp_path, mutation):
    b = NSEBrowser()
    b.records = [SELECTED]
    b.policy = AsyncMock()
    b._download = AsyncMock(side_effect=Failure('NSE_DOWNLOAD_FAILED', detail='ERR_HTTP2_PROTOCOL_ERROR'))
    try:
        with pytest.raises(Failure): b.download(dict(SELECTED, **mutation), tmp_path/'pending.zip')
        b.policy.assert_not_awaited()
        assert not list(tmp_path.iterdir())
    finally: b.close()


def fake_connection(monkeypatch, status=200, body=b'raw archive', headers=None):
    from calendar_evidence_acquisition import nse_transport
    import io
    response = io.BytesIO(body)
    response.status = status
    response.version = 11
    values = {'Content-Type':'application/zip', 'Content-Disposition':'attachment; filename="observed-example.zip"'}
    values.update(headers or {})
    response.getheader = lambda key, default=None: values.get(key, default)
    calls = []
    connection = SimpleNamespace(request=lambda *args, **kwargs:calls.append((args, kwargs)),
        getresponse=lambda:response, close=lambda:None, sock=None)
    def factory(host, **kwargs):
        assert host == 'nsearchives.nseindia.com'
        assert kwargs['context'].check_hostname
        assert kwargs['context'].verify_mode == nse_transport.ssl.CERT_REQUIRED
        return connection
    monkeypatch.setattr(nse_transport.http.client, 'HTTPSConnection', factory)
    return calls


def test_direct_http1_retains_actual_response_metadata_and_original_bytes(tmp_path, monkeypatch):
    from calendar_evidence_acquisition.nse_transport import download_http1
    calls = fake_connection(monkeypatch)
    p = tmp_path/'pending.zip'
    result = download_http1(URL, p)
    assert p.read_bytes() == b'raw archive'
    assert result['url'] == URL and result['status'] == 200
    assert result['filename'] == 'observed-example.zip' and result['http_protocol'] == 'HTTP/1.1'
    assert calls[0][0] == ('GET', '/observed-example.zip')


@pytest.mark.parametrize('status', [301,302,307,403,429,500])
def test_direct_http1_does_not_follow_redirects_or_accept_denials(tmp_path, monkeypatch, status):
    from calendar_evidence_acquisition.nse_transport import download_http1
    calls = fake_connection(monkeypatch, status=status, headers={'Location':'https://evil.test/'})
    with pytest.raises(Failure): download_http1(URL, tmp_path/'pending.zip')
    assert len(calls) == 1 and not list(tmp_path.iterdir())


def test_direct_http1_never_overwrites_existing_bytes(tmp_path, monkeypatch):
    from calendar_evidence_acquisition.nse_transport import download_http1
    calls = fake_connection(monkeypatch)
    p = tmp_path/'pending.zip'; p.write_bytes(b'preserved')
    with pytest.raises(Failure): download_http1(URL, p)
    assert p.read_bytes() == b'preserved' and not calls


def test_diagnostics_expose_only_allowlisted_error_codes():
    from calendar_evidence_acquisition.nse_transport import error_code
    assert error_code(Exception('Page.goto: net::ERR_HTTP2_PROTOCOL_ERROR\nCookie: secret')) == 'ERR_HTTP2_PROTOCOL_ERROR'
    assert error_code(Exception('token=secret')) == 'TRANSPORT_ERROR'


@pytest.mark.parametrize('status_reason', ['NSE_HTTP_403','NSE_HTTP_429','NSE_REDIRECT_REQUIRES_REVIEW','NSE_ROBOTS_DISALLOWED'])
def test_browser_download_denials_never_reach_direct_transport(tmp_path, status_reason):
    b=NSEBrowser(); b.records=[SELECTED]; b.policy=AsyncMock()
    b._download=AsyncMock(side_effect=Failure(status_reason))
    try:
        with pytest.raises(Failure, match=status_reason): b.download(SELECTED,tmp_path/'pending.zip')
        b.policy.assert_not_awaited()
    finally: b.close()


def test_direct_fallback_policy_denial_stops_before_network(tmp_path):
    b=NSEBrowser(); b.records=[SELECTED]
    b.policy=AsyncMock(side_effect=Failure('NSE_ROBOTS_DISALLOWED'))
    b._download=AsyncMock(side_effect=Failure('NSE_DOWNLOAD_FAILED',detail='ERR_HTTP2_PROTOCOL_ERROR'))
    try:
        with pytest.raises(Failure,match='NSE_ROBOTS_DISALLOWED'): b.download(SELECTED,tmp_path/'pending.zip')
        assert not list(tmp_path.iterdir())
    finally: b.close()


@pytest.mark.parametrize('headers', [{'Content-Length':'100'}, {'Content-Length':'-1'},
    {'Content-Length':'999999999999'}, {'Content-Type':'text/html'},
    {'Content-Disposition':'attachment; filename="../bad.zip"'}])
def test_invalid_http1_response_never_leaves_pending_bytes(tmp_path,monkeypatch,headers):
    from calendar_evidence_acquisition.nse_transport import download_http1
    fake_connection(monkeypatch,headers=headers)
    with pytest.raises(Failure): download_http1(URL,tmp_path/'pending.zip')
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('http1', [False,True])
def test_browser_launch_changes_protocol_only_and_keeps_tls_policy(monkeypatch,http1):
    import sys,types
    launch=AsyncMock(side_effect=Failure('STOP_AFTER_LAUNCH'))
    pw=SimpleNamespace(chromium=SimpleNamespace(launch=launch),stop=AsyncMock())
    module=types.ModuleType('playwright.async_api')
    module.async_playwright=lambda:SimpleNamespace(start=AsyncMock(return_value=pw))
    module.Error=type('PWError',(Exception,),{})
    module.TimeoutError=type('PWTimeout',(module.Error,),{})
    monkeypatch.setitem(sys.modules,'playwright',types.ModuleType('playwright'))
    monkeypatch.setitem(sys.modules,'playwright.async_api',module)
    b=NSEBrowser(); b.http1=http1
    try:
        with pytest.raises(Failure,match='STOP_AFTER_LAUNCH'): b.observe(None)
        expected=dict(headless=True,timeout=20000)
        if http1: expected['args']=['--disable-http2','--disable-quic']
        assert launch.call_args.kwargs==expected
    finally: b.close()
