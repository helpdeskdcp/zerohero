"""NSE discovery contracts. Observed URLs only; never generate archive endpoints."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import html
import re
from urllib.parse import urlsplit, parse_qsl

from .evidence_engine import guarded_url

PAGE = 'https://www.nseindia.com/all-reports-derivatives'
TITLE = 'F&O - UDiFF Common Bhavcopy Final (zip)'
IST = timezone(timedelta(hours=5, minutes=30))


class Failure(ValueError):
    def __init__(self, reason: str, *, transient=False, detail=None):
        super().__init__(reason)
        self.reason, self.transient, self.detail = reason, transient, detail


def normalized(value: str) -> str:
    return ' '.join(html.unescape(value).split()).casefold()


def safe_url(url: str, *, download=False) -> str:
    try:
        guarded_url(url, 'NSE')
        parts = urlsplit(url)
        if parts.port not in (None, 443) or parts.fragment:
            raise ValueError('URL_INVALID')
        if any(re.search('token|secret|password|signature|auth|cookie|api.?key', key, re.I)
               for key, _ in parse_qsl(parts.query)):
            raise ValueError('CREDENTIAL_URL')
        if download and parts.path.rstrip('/') in ('', '/all-reports-derivatives'):
            raise ValueError('PAGE_IS_NOT_DOWNLOAD')
    except (ValueError, TypeError, AttributeError) as exc:
        raise Failure('SOURCE_METADATA_REQUIRED', detail='OFFICIAL_DOWNLOAD_URL_REQUIRED') from exc
    return url


def checked_http(status: int):
    if status != 200:
        raise Failure('NSE_HTTP_' + str(status), transient=500 <= status <= 599)


def discover(proof: dict, requested: date | None, *, clock: datetime | None = None) -> dict:
    """Require same-record label/date/URL association in observed DOM or JSON.

    Latest-completed mode picks an observed Final report dated before today in IST.
    It does not infer a holiday, weekday, date of publication or intraday close.
    """
    if proof.get('page_url') != PAGE:
        raise Failure('SOURCE_METADATA_REQUIRED', detail='AUTHORITATIVE_PAGE_REQUIRED')
    checked_http(proof.get('page_status', 0))
    if not proof.get('page_body') or not proof.get('observed_at'):
        raise Failure('SOURCE_METADATA_REQUIRED')
    clock = clock or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise Failure('TIMEZONE_REQUIRED')
    candidates = {}
    for record in proof.get('records', []):
        if normalized(str(record.get('report_name', ''))) != normalized(TITLE):
            continue
        try:
            day = date.fromisoformat(record['trading_date'])
            url = safe_url(record['download_url'], download=True)
        except (KeyError, ValueError, TypeError):
            continue
        if record.get('method') not in {'DOM_LINK', 'PAGE_JSON', 'BROWSER_REQUEST'}:
            continue
        if requested is not None and day != requested:
            continue
        if requested is None and day >= clock.astimezone(IST).date():
            continue
        candidates[(day, url)] = dict(record, report_name=TITLE)
    if requested is None and candidates:
        latest = max(day for day, _ in candidates)
        candidates = {k:v for k,v in candidates.items() if k[0] == latest}
    if len(candidates) != 1:
        raise Failure('SOURCE_METADATA_REQUIRED', detail='NSE_REPORT_NOT_FOUND_OR_AMBIGUOUS')
    return next(iter(candidates.values()))


def json_records(value, response_url: str) -> list[dict]:
    """Parse explicit report objects in responses emitted by the official page.

    No requests are synthesized from field names or JavaScript templates. Unknown
    response layouts stay unsupported until retained real evidence can be reviewed.
    """
    safe_url(response_url)
    found = []
    def walk(node):
        if isinstance(node, list):
            for child in node: walk(child)
        elif isinstance(node, dict):
            labels = [v for v in node.values() if isinstance(v,str) and normalized(v)==normalized(TITLE)]
            urls = [v for k,v in node.items() if k.lower() in {'url','downloadurl','download_url','fileurl','file_url'} and isinstance(v,str)]
            dates = [v for k,v in node.items() if k.lower() in {'date','trading_date','tradingdate','reportdate','report_date'} and isinstance(v,str)]
            if len(labels)==len(urls)==len(dates)==1:
                found.append(dict(report_name=labels[0], download_url=urls[0], trading_date=dates[0],
                                  method='PAGE_JSON', response_url=response_url))
            for child in node.values():
                if isinstance(child,(dict,list)): walk(child)
    walk(value)
    return found
