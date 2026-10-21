"""Pure age, source-selection and discrepancy contracts; never merge or mutate feeds."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import math
from .core import DataError, SOURCE, instant, number


def freshness(source_timestamp, received_timestamp, *, live_seconds=2, stale_seconds=60) -> dict:
    if not math.isfinite(live_seconds) or not math.isfinite(stale_seconds) or not 0 <= live_seconds <= stale_seconds:
        raise ValueError('INVALID_AGE_THRESHOLDS')
    out=dict(status='DATA_UNAVAILABLE',reason='SOURCE_TIMESTAMP_UNAVAILABLE',data_age_seconds=None,
             source_timestamp=source_timestamp,received_timestamp=received_timestamp)
    try: age=(instant(received_timestamp)-instant(source_timestamp)).total_seconds()
    except DataError: return out
    if age < 0: return dict(out,reason='FUTURE_SOURCE_TIMESTAMP')
    return dict(out,status='LIVE' if age<=live_seconds else 'RECENT' if age<=stale_seconds else 'STALE',
                reason=None if age<=stale_seconds else 'STALE_SOURCE',data_age_seconds=age)


def compare_sources(nse: dict, broker: dict) -> dict:
    out: dict = dict(status='DATA_UNAVAILABLE',reason=None,nse_source=nse.get('source'),broker_source=broker.get('source'),
             nse_timestamp=nse.get('source_timestamp'),broker_timestamp=broker.get('source_timestamp'),
             age_difference=None,differences={},unavailable_fields={},identity_differences={},
             nse_evidence=nse.get('source_hash'),broker_evidence=broker.get('source_hash'))
    if nse.get('source')!=SOURCE or broker.get('source') not in {'DHAN','ANGELONE'}:
        return dict(out,reason='SOURCE_ATTRIBUTION_REQUIRED')
    identity=('exchange','symbol','instrument_type','expiry','strike','option_type','timeframe','data_timestamp')
    if any(nse.get(k) is None or broker.get(k) is None for k in identity): return dict(out,reason='CONTRACT_IDENTITY_REQUIRED')
    mismatch={k:dict(nse=nse[k],broker=broker[k]) for k in identity if nse[k]!=broker[k]}
    if mismatch: return dict(out,status='DATA_DISCREPANCY',reason='CONTRACT_MISMATCH',identity_differences=mismatch)
    out['comparison_scope']='SESSION_AGGREGATES_NOT_SIMULTANEOUS_QUOTES' if nse['timeframe']=='1d' else 'EXACT_TIMESTAMP'
    if nse['timeframe']!='1d':
        try:
            if instant(nse.get('source_timestamp'))!=instant(broker.get('source_timestamp')):
                return dict(out,reason='SOURCE_TIMESTAMP_MISMATCH')
        except DataError: return dict(out,reason='SOURCE_TIMESTAMP_REQUIRED')
    try: out['age_difference']=number(nse.get('data_age_seconds'),signed=True)-number(broker.get('data_age_seconds'),signed=True)
    except DataError: pass
    for field in ('ltp','open','high','low','close','volume','oi','oi_change'):
        if field in {'volume','oi','oi_change'} and (not nse.get('quantity_unit') or nse.get('quantity_unit')!=broker.get('quantity_unit')):
            out['unavailable_fields'][field]='QUANTITY_UNITS_NOT_ALIGNED';continue
        if field in {'ltp','open','high','low'} and (nse.get('price_status')=='NO_TRADES_REPORTED' or broker.get('price_status')=='NO_TRADES_REPORTED'):
            out['unavailable_fields'][field]='NO_TRADES_REPORTED';continue
        try: out['differences'][field]=number(nse.get(field),signed=field=='oi_change')-number(broker.get(field),signed=field=='oi_change')
        except DataError: out['unavailable_fields'][field]='VALUE_UNAVAILABLE'
    if not out['differences']: return dict(out,reason='COMPARABLE_FIELDS_UNAVAILABLE')
    return dict(out,status='DATA_DISCREPANCY' if any(out['differences'].values()) else 'MATCH')


def select_source(official: dict, brokers: list[dict], *, provider=None, allow_fallback=False, for_live=False,
                  live_seconds=2, stale_seconds=60, as_of=None) -> dict:
    requested=SOURCE if provider=='NSE' else provider
    order=[requested] if requested else [SOURCE]+(['DHAN','ANGELONE'] if allow_fallback else [])
    available=[official,*brokers]
    for source in order:
        if source not in {SOURCE,'DHAN','ANGELONE'}: break
        matches=[r for r in available if r.get('source')==source]
        if len(matches)!=1: continue
        result=matches[0]
        if result.get('status')!='OK' or not result.get('rows') or any(r.get('source')!=source for r in result['rows']): continue
        if official.get('contract_request') and result.get('contract_request')!=official['contract_request']: continue
        if for_live:
            age=freshness(result.get('source_timestamp'),result.get('received_timestamp'),live_seconds=live_seconds,stale_seconds=stale_seconds)
            if age['status'] not in ('LIVE','RECENT'): continue
            current=freshness(result.get('source_timestamp'),as_of or datetime.now(timezone.utc),live_seconds=live_seconds,stale_seconds=stale_seconds)
            if current['status'] not in ('LIVE','RECENT'): continue
            if instant(result['received_timestamp'])>instant(as_of or datetime.now(timezone.utc)): continue
        out=deepcopy(result);role='PRIMARY' if source==SOURCE else 'EXPLICIT_PROVIDER' if requested else 'FALLBACK'
        out.update(source_role=role,fallback_used=role=='FALLBACK',fallback_reason=official.get('reason') if role=='FALLBACK' else None)
        if for_live:
            out.update(data_age_seconds=age['data_age_seconds'],freshness_status=current['status'],current_age_seconds=current['data_age_seconds'])
        for row in out['rows']: row.update(source_role=role,fallback_used=role=='FALLBACK')
        return out
    return dict(source=requested,status='DATA_UNAVAILABLE',reason='NO_VALID_PROVIDER_DATA',rows=[],fallback_used=False)
