"""Strict interpretation of the captured official NSE option-chain-v3 schema."""
from datetime import datetime

from .core import DataError, SOURCE, IST, day, instant, json_value, number, sha


def source_time(raw: bytes) -> str:
    obj = json_value(raw)
    try:
        # NSE's retained official rendering script appends IST to this field.
        timestamp = datetime.strptime(obj['records']['timestamp'], '%d-%b-%Y %H:%M:%S')
    except (KeyError, TypeError, ValueError) as exc:
        raise DataError('SOURCE_TIMESTAMP_UNAVAILABLE') from exc
    return instant(timestamp.replace(tzinfo=IST)).isoformat()


def normalize_chain(raw: bytes, request: dict, endpoint: str) -> tuple[list[dict], list[dict]]:
    obj = json_value(raw)
    if not isinstance(obj, dict) or not isinstance(obj.get('records'), dict) or not isinstance(obj.get('filtered'), dict):
        raise DataError('INVALID_SCHEMA')
    records, filtered = obj['records'], obj['filtered']
    data = records.get('data')
    if not isinstance(data, list) or not data:
        raise DataError('EMPTY_DATASET')
    if filtered.get('data') != data:
        raise DataError('CHAIN_FILTER_MISMATCH')
    if not isinstance(records.get('expiryDates'), list) or request['expiry'] not in [day(d) for d in records['expiryDates']]:
        raise DataError('CONTRACT_MISMATCH')
    timestamp = source_time(raw)
    if instant(timestamp).astimezone(IST).date().isoformat() > request['expiry']:
        raise DataError('POST_EXPIRY_OBSERVATION')
    underlying = number(records.get('underlyingValue'))
    if underlying <= 0:
        raise DataError('INVALID_NUMERIC')
    seen, rows, contracts = set(), [], []
    fields = {'ltp':'lastPrice', 'oi':'openInterest', 'oi_change':'changeinOpenInterest',
              'volume':'totalTradedVolume', 'iv_as_reported':'impliedVolatility'}
    for item in data:
        if not isinstance(item, dict) or day(item.get('expiryDates')) != request['expiry']:
            raise DataError('CONTRACT_MISMATCH')
        strike = number(item.get('strikePrice'))
        if strike <= 0:
            raise DataError('INVALID_NUMERIC')
        for side in ('CE', 'PE'):
            leg = item.get(side)
            if not isinstance(leg, dict) or not leg.get('identifier') or not leg.get('expiryDate') or not leg.get('underlying'):
                raise DataError('INCOMPLETE_CHAIN_CONTRACT')
            if (leg['underlying'] != request['symbol'] or day(leg['expiryDate']) != request['expiry']
                    or number(leg.get('strikePrice')) != strike
                    or not isinstance(leg['identifier'], str)
                    or not leg['identifier'].startswith('OPTIDX' + request['symbol'])
                    or leg.get('optionType') not in (None, side)):
                raise DataError('CONTRACT_MISMATCH')
            identity = (request['expiry'], strike, side)
            if identity in seen:
                raise DataError('DUPLICATE_CONTRACT')
            seen.add(identity)
            values = {key:number(leg.get(field), signed=key=='oi_change', integer=key in {'oi','oi_change','volume'})
                      for key, field in fields.items()}
            if values['ltp'] <= 0:
                raise DataError('QUOTE_UNAVAILABLE')
            if number(leg.get('underlyingValue')) != underlying:
                raise DataError('UNDERLYING_MISMATCH')
            row = dict(source=SOURCE, exchange='NSE', segment='FO', symbol=request['symbol'],
                       underlying=request['symbol'], instrument_type='OPTIDX', option_type=side,
                       strike=strike, expiry=request['expiry'], identifier=leg['identifier'],
                       source_timestamp=timestamp, data_timestamp=timestamp, timeframe='SNAPSHOT',
                       underlying_value=underlying, source_url=endpoint, source_hash=sha(raw),
                       quantity_unit='NSE_CHAIN_REPORTED_QUANTITY', iv_units='UNVERIFIED',
                       open=None, high=None, low=None, close=None, **values)
            row['unavailable_fields'] = {key:'NOT_PUBLISHED_IN_RESPONSE' for key in ('open','high','low','close')}
            for key, field in {'bid_price':'buyPrice1', 'ask_price':'sellPrice1',
                               'bid_quantity':'buyQuantity1', 'ask_quantity':'sellQuantity1'}.items():
                value = leg.get(field)
                row[key] = None if value is None else number(value, integer=key.endswith('quantity'))
                if value is None:
                    row['unavailable_fields'][key] = 'NOT_PUBLISHED_IN_RESPONSE'
            rows.append(row)
            contracts.append({**{key:row[key] for key in ('source','exchange','segment','symbol','underlying','instrument_type',
                               'option_type','strike','expiry','source_timestamp','source_url','source_hash')},
                              'identifier':leg['identifier'], 'token_if_available':None, 'lot_size_if_available':None,
                              'status':'OBSERVED_CONTRACT_NOT_COMPLETE_MASTER'})
    for side in ('CE', 'PE'):
        totals = filtered.get(side)
        if not isinstance(totals, dict):
            raise DataError('CHAIN_TOTAL_MISMATCH')
        for total, field in (('totOI','oi'), ('totVol','volume')):
            if number(totals.get(total), integer=True) != sum(r[field] for r in rows if r['option_type']==side):
                raise DataError('CHAIN_TOTAL_MISMATCH')
    return rows, contracts
