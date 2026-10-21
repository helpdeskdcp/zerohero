"""Explicit quality decisions and a review-only production gate. No activation API."""
from pathlib import Path
import math
from .core import DataError, instant, json_value, sha
from .quality import compare_sources, freshness

from ..hist_options.calendar_scope import calendar_disabled

GATES = ('full_suite','contract_master','expiry_calendar','historical_ingestion',
         'live_freshness','provenance','duplicates','discrepancy')


def validation_status(result: dict) -> str:
    if result.get('status')=='SOURCE_METADATA_REQUIRED': return 'SOURCE_METADATA_REQUIRED'
    if result.get('reason')=='INCOMPLETE_CHAIN_CONTRACT': return 'PLACEHOLDER'
    if result.get('reason')=='STALE_SOURCE': return 'STALE'
    if result.get('status')!='OK': return 'DATA_UNAVAILABLE'
    if result.get('dataset_type')=='historical': return 'VALID_HISTORICAL'
    if result.get('dataset_type')!='option_chain': return 'OBSERVED_METADATA'
    return {'LIVE':'VALID_LIVE','RECENT':'VALID_RECENT','STALE':'STALE'}.get(str(result.get('freshness_status')),'DATA_UNAVAILABLE')


def compare_market_sources(nse: dict, broker: dict, *, as_of, minor_tolerances=None, stale_seconds=60) -> dict:
    """Explicit absolute minor tolerances; absent tolerance means any difference is major."""
    limits=minor_tolerances or {}
    if any(isinstance(v,bool) or not math.isfinite(v) or v<0 for v in limits.values()):
        raise ValueError('INVALID_DISCREPANCY_TOLERANCE')
    out: dict = dict(STATUS='MAPPING_MISMATCH',NSE_TIMESTAMP=nse.get('source_timestamp'),
             BROKER_TIMESTAMP=broker.get('source_timestamp'),AGE_DIFFERENCE=None,fields={},reason=None)
    for data in (nse,broker):
        age=freshness(data.get('source_timestamp'),as_of,live_seconds=0,stale_seconds=stale_seconds)
        if age['status']=='STALE':return dict(out,STATUS='STALE_SOURCE',reason='STALE_SOURCE')
        if age['status']=='DATA_UNAVAILABLE':return dict(out,reason=age['reason'])
        try:
            if instant(data.get('received_timestamp'))>instant(as_of):return dict(out,reason='FUTURE_RECEIVED_TIMESTAMP')
        except DataError:return dict(out,reason='RECEIVED_TIMESTAMP_REQUIRED')
    comparison=compare_sources(nse,broker)
    if comparison['reason'] or comparison['unavailable_fields']:
        return dict(out,reason=comparison['reason'] or 'REQUIRED_FIELD_OR_UNIT_UNAVAILABLE')
    na=(instant(as_of)-instant(nse['source_timestamp'])).total_seconds()
    ba=(instant(as_of)-instant(broker['source_timestamp'])).total_seconds()
    out['AGE_DIFFERENCE']=na-ba
    for key,difference in comparison['differences'].items():
        out['fields'][key]=dict(NSE_VALUE=nse[key],BROKER_VALUE=broker[key],DIFFERENCE=difference,
                               NSE_TIMESTAMP=out['NSE_TIMESTAMP'],BROKER_TIMESTAMP=out['BROKER_TIMESTAMP'],
                               AGE_DIFFERENCE=na-ba)
    differences=comparison['differences']
    out['STATUS']=('MATCH' if not any(differences.values()) else
                   'MINOR_DISCREPANCY' if all(abs(v)<=limits.get(k,0) for k,v in differences.items()) else 'MAJOR_DISCREPANCY')
    for field in out['fields'].values():field['STATUS']=out['STATUS']
    return out


def production_gate(checks: dict | None = None, *, exchange: str = "NSE") -> dict:
    """Hash-bound reviewed reports can qualify a preview, never authorize deployment."""
    if calendar_disabled(exchange):
        return dict(PRODUCTION='NOT_ENABLED', **{'AUTO/NEXT':'DISABLED'}, LIVE_ORDERS='DISABLED',
                    eligible_for_review=False, missing_gates=[], pending_requirements=[],
                    gate_required=False, scope='OUT_OF_SCOPE', status='DISABLED',
                    reason='CALENDAR_EXCHANGE_DISABLED', calendar_installed=False)
    missing=[]
    for name in GATES:
        check=(checks or {}).get(name,{})
        try:
            path=Path(check['evidence_path'])
            if path.is_symlink():raise ValueError()
            raw=path.read_bytes();report=json_value(raw)
            if not isinstance(report,dict):raise ValueError()
            if (sha(raw)!=check['sha256'] or check.get('independently_reviewed') is not True
                    or report.get('status')!='PASS' or report.get('complete') is not True):raise ValueError()
            if name=='full_suite' and any(report.get(k)!=0 for k in ('unexpected_failures','errors','timeouts')):
                raise ValueError()
        except (OSError,KeyError,TypeError,ValueError):missing.append(name)
    return dict(PRODUCTION='BLOCKED',**{'AUTO/NEXT':'FAIL-CLOSED'},LIVE_ORDERS='DISABLED',
                eligible_for_review=not missing,missing_gates=missing,reason='MANUAL_ACTIVATION_NOT_AUTHORIZED',
                calendar_installed=False)
