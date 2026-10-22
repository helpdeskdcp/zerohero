"""Bounded HTTP/1.1 archive transport; caller must establish discovery and policy.

This module never discovers or constructs archive URLs. It only transfers an exact
observed HTTPS archive URL, without redirects, credentials or TLS exceptions.
"""
from __future__ import annotations

from email.message import Message
import http.client
import json
import logging
from pathlib import Path
import re
import ssl
import time
from urllib.parse import urlsplit, unquote

from .evidence_engine import ROBOT_AGENT, now
from .nse_discovery import Failure, safe_url, checked_http
from .static_archives import MAX_BYTES

TRANSPORT_CODES = {
    'ERR_HTTP2_PROTOCOL_ERROR', 'ERR_CONNECTION_RESET', 'ERR_CONNECTION_CLOSED',
    'ERR_CONNECTION_TIMED_OUT', 'ERR_NAME_NOT_RESOLVED',
    'ERR_CERT_AUTHORITY_INVALID', 'ERR_CERT_DATE_INVALID', 'ERR_CERT_COMMON_NAME_INVALID',
}


def error_code(exc: object) -> str:
    """Never return exception text containing browser headers/cookies/URLs."""
    for code in re.findall(r'\bERR_[A-Z0-9_]+\b', str(exc)):
        if code in TRANSPORT_CODES:
            return code
    return 'TRANSPORT_ERROR'


def diagnostic(event: str, **fields) -> None:
    logging.getLogger(__name__).warning(json.dumps(dict(event=event, **fields)))


def download_http1(url: str, destination: Path) -> dict:
    url = safe_url(url, download=True)
    parts = urlsplit(url)
    if parts.hostname not in {'nsearchives.nseindia.com', 'archives.nseindia.com'}:
        raise Failure('SOURCE_METADATA_REQUIRED', detail='OBSERVED_ARCHIVE_HOST_REQUIRED')
    if destination.exists() or destination.is_symlink():
        raise Failure('NSE_ARTIFACT_CONFLICT')
    context = ssl.create_default_context()
    context.set_alpn_protocols(['http/1.1'])
    conn = http.client.HTTPSConnection(parts.hostname, timeout=15, context=context)
    started = time.monotonic()
    created = False
    try:
        target = parts.path or '/'
        if parts.query: target += '?' + parts.query
        conn.request('GET', target, headers={'User-Agent': ROBOT_AGENT+'/1', 'Accept': 'application/zip'})
        response = conn.getresponse()
        if 300 <= response.status < 400:
            raise Failure('NSE_REDIRECT_REQUIRES_REVIEW')
        checked_http(response.status)
        if response.version != 11:
            raise Failure('NSE_HTTP_PROTOCOL_UNEXPECTED')
        size = response.getheader('Content-Length')
        if size is not None and (not size.isdigit() or int(size) > MAX_BYTES):
            raise Failure('NSE_ARCHIVE_INVALID')
        content_type = response.getheader('Content-Type', '')
        if 'html' in content_type.lower(): raise Failure('NSE_ARCHIVE_INVALID')
        header = Message()
        header['Content-Disposition'] = response.getheader('Content-Disposition', '')
        filename = header.get_filename() or unquote(parts.path.rsplit('/', 1)[-1])
        if not filename or Path(filename).name != filename or '\\' in filename:
            raise Failure('NSE_FILENAME_INVALID')
        tls = conn.sock.version() if conn.sock else None
        total = 0
        with destination.open('xb') as stream:
            created = True
            while True:
                if time.monotonic() - started > 45: raise Failure('NSE_HTTP1_TIMEOUT')
                chunk = response.read(min(65536, MAX_BYTES - total + 1))
                if not chunk: break
                total += len(chunk)
                if total > MAX_BYTES: raise Failure('NSE_ARCHIVE_INVALID')
                stream.write(chunk)
        if total == 0 or (size is not None and total != int(size)):
            raise Failure('NSE_ARCHIVE_INVALID')
        return dict(url=url, filename=filename, status=response.status, content_type=content_type,
                    http_protocol='HTTP/1.1', tls_protocol=tls, downloaded_at=now(), redirects=[])
    except (OSError, http.client.HTTPException) as exc:
        reason = 'NSE_TLS_VALIDATION_FAILED' if isinstance(exc, ssl.SSLCertVerificationError) else 'NSE_HTTP1_TRANSPORT_FAILED'
        raise Failure(reason, detail=type(exc).__name__) from exc
    finally:
        conn.close()
        # Pending bytes belong only to this attempt. Never touch existing evidence.
        import sys
        if created and sys.exc_info()[0] is not None:
            destination.unlink(missing_ok=True)
