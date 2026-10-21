"""Strict parsing and normalization of observed NSE datasets, never a calendar rule."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math

SOURCE = 'NSE_OFFICIAL'
IST = timezone(timedelta(hours=5, minutes=30))
MAX_BYTES = 8_000_000


class DataError(ValueError):
    pass


def sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def json_value(raw: bytes):
    def constant(value): raise DataError('INVALID_JSON')
    def pairs(items):
        out = {}
        for key, value in items:
            if key in out: raise DataError('DUPLICATE_JSON_KEY')
            out[key] = value
        return out
    if len(raw) > MAX_BYTES: raise DataError('RESPONSE_TOO_LARGE')
    try: return json.loads(raw, parse_constant=constant, object_pairs_hook=pairs)
    except (ValueError, UnicodeError) as exc:
        if isinstance(exc, DataError): raise
        raise DataError('INVALID_JSON') from exc


def instant(value) -> datetime:
    try:
        d = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace('Z', '+00:00'))
        if d.tzinfo is None or d.utcoffset() is None: raise ValueError()
        return d.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc: raise DataError('TIMESTAMP_UNAVAILABLE') from exc


def day(value) -> str:
    if not isinstance(value, str): raise DataError('INVALID_DATE')
    for fmt in ('%Y-%m-%d', '%d-%b-%Y', '%d-%m-%Y'):
        try: return datetime.strptime(value, fmt).date().isoformat()
        except ValueError: pass
    raise DataError('INVALID_DATE')


def number(value, *, signed=False, integer=False):
    if isinstance(value, bool) or value is None: raise DataError('INVALID_NUMERIC')
    try: n = float(value)
    except (ValueError, TypeError, OverflowError) as exc: raise DataError('INVALID_NUMERIC') from exc
    if not math.isfinite(n) or (not signed and n < 0) or (integer and not n.is_integer()):
        raise DataError('INVALID_NUMERIC')
    return int(n) if integer else n


FIELDS = {'open':'FH_OPENING_PRICE', 'high':'FH_TRADE_HIGH_PRICE', 'low':'FH_TRADE_LOW_PRICE',
          'close':'FH_CLOSING_PRICE', 'ltp':'FH_LAST_TRADED_PRICE', 'settlement_price':'FH_SETTLE_PRICE',
          'volume':'FH_TOT_TRADED_QTY', 'oi':'FH_OPEN_INT', 'oi_change':'FH_CHANGE_IN_OI',
          'underlying_value':'FH_UNDERLYING_VALUE'}


def normalize_historical(raw: bytes, request: dict, endpoint: str) -> tuple[list[dict], list[dict]]:
    obj = json_value(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get('data'), list): raise DataError('INVALID_SCHEMA')
    if not obj['data']: raise DataError('EMPTY_DATASET')
    if obj.get('error') or obj.get('hasMore') or obj.get('nextPage'): raise DataError('INCOMPLETE_RESPONSE')
    if 'totalRecords' in obj and obj['totalRecords'] != len(obj['data']): raise DataError('INCOMPLETE_RESPONSE')
    rows, contracts, seen = [], [], set()
    for item in obj['data']:
        if not isinstance(item, dict): raise DataError('INVALID_SCHEMA')
        if not item.get('FH_EXPIRY_DT'): raise DataError('MISSING_EXPIRY')
        required = {'FH_INSTRUMENT','FH_SYMBOL','FH_STRIKE_PRICE','FH_OPTION_TYPE','FH_TIMESTAMP', *FIELDS.values()}
        if not required <= item.keys(): raise DataError('MISSING_FIELD')
        expiry = day(item['FH_EXPIRY_DT']); trade_date = day(item['FH_TIMESTAMP'])
        if (item['FH_SYMBOL'], item['FH_INSTRUMENT'], item['FH_OPTION_TYPE'], expiry) != (
            request['symbol'], request['instrument_type'], request['option_type'], request['expiry']):
            raise DataError('CONTRACT_MISMATCH')
        if trade_date != request['trade_date'] or trade_date > expiry: raise DataError('TIMESTAMP_MISMATCH')
        # This field is a date-sort key. Check consistency but NEVER call it quote time.
        if item.get('FH_TIMESTAMP_ORDER') is not None:
            if instant(item['FH_TIMESTAMP_ORDER']).astimezone(IST).date().isoformat() != trade_date:
                raise DataError('TIMESTAMP_MISMATCH')
        strike = number(item['FH_STRIKE_PRICE'])
        if strike <= 0: raise DataError('INVALID_NUMERIC')
        identity = (trade_date, expiry, strike, item['FH_OPTION_TYPE'])
        if identity in seen: raise DataError('DUPLICATE_CONTRACT')
        seen.add(identity)
        values = {k:number(item[v], signed=k=='oi_change', integer=k in {'volume','oi','oi_change'}) for k,v in FIELDS.items()}
        if values['volume'] > 0 and (values['high'] < max(values['open'],values['low'],values['close'])
                                    or values['low'] > min(values['open'],values['close'])):
            raise DataError('INVALID_OHLC')
        lot = None if item.get('FH_MARKET_LOT') is None else number(item['FH_MARKET_LOT'],integer=True)
        if lot is not None and lot <= 0: raise DataError('INVALID_NUMERIC')
        row = dict(exchange='NSE', segment='FO', symbol=item['FH_SYMBOL'], underlying=item['FH_SYMBOL'],
                   instrument_type=item['FH_INSTRUMENT'], strike=strike, option_type=item['FH_OPTION_TYPE'],
                   expiry=expiry, trade_date=trade_date, data_timestamp=trade_date,
                   data_timestamp_precision='SESSION_DATE', source_timestamp=None, timeframe='1d',
                   source=SOURCE, source_url=endpoint, source_hash=sha(raw), **values)
        row.update(price_status='AS_REPORTED' if values['volume'] else 'NO_TRADES_REPORTED',
                   quantity_unit='NSE_REPORTED_QUANTITY', raw_timestamp_order=item.get('FH_TIMESTAMP_ORDER'))
        rows.append(row)
        contracts.append({**{k:row[k] for k in ('exchange','segment','symbol','underlying','instrument_type','strike','option_type','expiry','source')},
                          'token_if_available':None, 'lot_size_if_available':lot, 'source_timestamp':None,
                          'valid_on_trade_date':trade_date, 'source_url':endpoint, 'source_hash':sha(raw),
                          'status':'OBSERVED_CONTRACT_NOT_COMPLETE_MASTER'})
    return sorted(rows,key=lambda r:r['strike']), sorted(contracts,key=lambda r:r['strike'])
