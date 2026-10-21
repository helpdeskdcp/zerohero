"""Optional GET-only HTTP/1.1 transport for already registered observations.

No URL discovery, templates, cookies, credentials, proxies, redirects or retries.
The existing acquisition layer owns discovery; this layer cannot extend its scope.
"""
from datetime import datetime, timezone
import http.client
import ssl
import time
from urllib.parse import urlsplit

from calendar_evidence_acquisition.evidence_engine import robots_policy, ROBOT_AGENT, BrowserUnavailable
from .core import DataError, MAX_BYTES, sha
from .provenance import Observation, Response, SourceRegistry, official_url
from .store import NSEStore


class OfficialHTTPTransport:
    def __init__(self, registry: SourceRegistry, store: NSEStore):
        self.registry = registry
        self.store = store

    def _get(self, url: str) -> Response:
        official_url(url)
        parts = urlsplit(url)
        context = ssl.create_default_context()
        context.set_alpn_protocols(['http/1.1'])
        connection = http.client.HTTPSConnection('www.nseindia.com', timeout=10, context=context)
        started = time.monotonic()
        try:
            target = parts.path + ('?' + parts.query if parts.query else '')
            connection.request('GET', target, headers={'User-Agent': ROBOT_AGENT + '/1', 'Accept': 'application/json,text/plain'})
            response = connection.getresponse()
            if 300 <= response.status < 400:
                raise DataError('REDIRECT_REJECTED')
            size = response.getheader('Content-Length')
            if size is not None and (not size.isdigit() or int(size) > MAX_BYTES):
                raise DataError('RESPONSE_SIZE_INVALID')
            chunks = []
            total = 0
            while True:
                if time.monotonic() - started > 30:
                    raise DataError('TRANSPORT_TIMEOUT')
                chunk = response.read1(min(65536, MAX_BYTES - total + 1))
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_BYTES:
                    raise DataError('RESPONSE_TOO_LARGE')
                chunks.append(chunk)
            if size is not None and total != int(size):
                raise DataError('TRUNCATED_RESPONSE')
            raw = b''.join(chunks)
            return Response(raw, response.status, datetime.now(timezone.utc).isoformat(),
                            url, response.getheader('Content-Type', ''), sha(raw),
                            response_headers={k:response.getheader(k) for k in
                                ('Content-Type','Content-Length','Date','ETag','Last-Modified') if response.getheader(k) is not None})
        except ssl.SSLCertVerificationError as exc:
            raise DataError('TLS_VALIDATION_FAILED') from exc
        except (OSError, http.client.HTTPException) as exc:
            raise DataError('HTTP_TRANSPORT_FAILED') from exc
        finally:
            connection.close()

    def __call__(self, observation: Observation) -> Response:
        if observation not in self.registry.observations:
            raise DataError('EXACT_OBSERVED_ENDPOINT_REQUIRED')
        official_url(observation.endpoint)
        policy_url = 'https://www.nseindia.com/robots.txt'
        policy = self._get(policy_url)
        policy_metadata = dict(source='NSE_ROBOTS_POLICY', endpoint=policy_url,
                               response_status=policy.status, received_timestamp=policy.received_timestamp)
        self.store.capture(policy.raw, policy_metadata)
        try:
            rules = robots_policy(policy.status, policy.raw.decode('utf-8'), 'NSE')
        except (BrowserUnavailable, UnicodeError) as exc:
            raise DataError('ROBOTS_POLICY_UNAVAILABLE') from exc
        if not rules.can_fetch(ROBOT_AGENT, observation.endpoint):
            raise DataError('ROBOTS_DENIED')
        response = self._get(observation.endpoint)
        return Response(response.raw, response.status, response.received_timestamp, response.endpoint,
                        response.content_type, response.sha256,
                        dict(policy_metadata, sha256=sha(policy.raw), result='ALLOWED', http_protocol='HTTP/1.1'),
                        response.response_headers)
