"""NSE official observation adapter. Unknown routes/schemas fail before network I/O."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable

from .core import DataError, SOURCE, IST, day, instant, json_value, normalize_historical
from .provenance import Observation, Response, SourceRegistry
from .store import NSEStore
from .chain import normalize_chain, source_time
from .quality import freshness
from .controls import validation_status


class NSEAdapter:
    def __init__(self, registry: SourceRegistry, store: NSEStore, *,
                 transport: Callable[[Observation], Response] | None = None,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
                 live_seconds: float = 2, stale_seconds: float = 60):
        self.registry,self.store,self.transport,self.clock=registry,store,transport,clock
        self.live_seconds,self.stale_seconds=live_seconds,stale_seconds
        freshness(None,None,live_seconds=live_seconds,stale_seconds=stale_seconds)

    def result(self, kind, request):
        return dict(source=SOURCE,exchange='NSE',segment='FO',endpoint=None,request_parameters={},response_status=None,
                    data_timestamp=None,source_timestamp=None,fetch_timestamp=None,received_timestamp=None,
                    processing_timestamp=instant(self.clock()).isoformat(),data_age_seconds=None,
                    freshness_status='DATA_UNAVAILABLE',freshness_reason='SOURCE_TIMESTAMP_NOT_PUBLISHED',
                    expiry=request.get('expiry'),symbol=request.get('symbol'),instrument_type=request.get('instrument_type'),
                    option_type=request.get('option_type'),rows=[],contracts=[],row_count=0,provenance={},sha256=None,
                    status='SOURCE_METADATA_REQUIRED',validation_status='SOURCE_METADATA_REQUIRED',reason='EXACT_OBSERVED_ENDPOINT_REQUIRED',dataset_type=kind,
                    contract_request=request,calendar_status='NOT INSTALLED',auto_next='FAIL-CLOSED')

    def _fetch(self, kind: str, request: dict, *, expected_row_count: int | None = None) -> dict:
        out=self.result(kind,request)
        try: observation=self.registry.find(kind,request)
        except DataError: return out
        out.update(endpoint=observation.endpoint,request_parameters=observation.parameters)
        try:
            r=self.transport(observation) if self.transport else Response(
                raw=observation.raw,status=observation.metadata['http_status'],
                received_timestamp=observation.metadata['observed_at'],endpoint=observation.endpoint,
                content_type=observation.metadata['content_type'],sha256=observation.metadata['sha256'],
                response_headers=observation.metadata.get('response_headers'))
            out['processing_timestamp']=instant(self.clock()).isoformat()
            if r.endpoint!=observation.endpoint: raise DataError('ENDPOINT_MISMATCH')
            received=instant(r.received_timestamp)
            if received>instant(out['processing_timestamp']): raise DataError('FUTURE_RECEIVED_TIMESTAMP')
            provenance=dict(source=SOURCE,endpoint=r.endpoint,request_parameters=observation.parameters,
                            response_status=r.status,content_type=r.content_type,received_timestamp=r.received_timestamp,
                            discovery_sources=observation.metadata['discovery_sources'],
                            discovery_observed_at=observation.metadata['observed_at'],
                            acquisition='LIVE_HTTP' if self.transport else 'RETAINED_OBSERVATION',
                            transport_evidence=r.transport_evidence,
                            response_headers={k:v for k,v in (r.response_headers or {}).items() if k.lower() in
                                {'content-type','content-length','date','etag','last-modified'}},
                            independent_calendar_review=False)
            digest=self.store.capture(r.raw,provenance)  # Original bytes before parsing or normalization.
            out.update(response_status=r.status,fetch_timestamp=r.received_timestamp,received_timestamp=r.received_timestamp,
                       sha256=digest,provenance=provenance)
            if r.sha256 is not None and digest!=r.sha256: raise DataError('SHA256_MISMATCH')
            if r.status!=200: raise DataError('HTTP_'+str(r.status))
            if 'json' not in r.content_type.lower(): raise DataError('CONTENT_TYPE_INVALID')
            if kind=='historical':
                if day(request['trade_date'])>received.astimezone(IST).date().isoformat(): raise DataError('FUTURE_DATA_TIMESTAMP')
                rows,contracts=normalize_historical(r.raw,request,r.endpoint)
                if expected_row_count is not None and len(rows)!=expected_row_count: raise DataError('INCOMPLETE_RESPONSE')
                out.update(rows=rows,contracts=contracts,data_timestamp=request['trade_date'],
                           data_timestamp_precision='SESSION_DATE',coverage_scope='EXACT_RESPONSE_ONLY',
                           contract_universe_complete=False)
                self.store.cache_observations([dict(exchange='NSE',symbol=request['symbol'],
                    instrument=request['instrument_type'],expiry=request['expiry'],
                    observed_trade_date=request['trade_date'],source=SOURCE,source_url=r.endpoint,
                    observed_at=r.received_timestamp,source_hash=digest,status='OBSERVED_NOT_CALENDAR_VERIFIED')],provenance)
            elif kind=='option_chain':
                timestamp=source_time(r.raw)
                age=freshness(timestamp,r.received_timestamp,live_seconds=self.live_seconds,stale_seconds=self.stale_seconds)
                current=freshness(timestamp,out['processing_timestamp'],live_seconds=self.live_seconds,stale_seconds=self.stale_seconds)
                out.update(source_timestamp=timestamp,data_timestamp=timestamp,data_timestamp_precision='SECOND',
                           data_age_seconds=age['data_age_seconds'],current_age_seconds=current['data_age_seconds'],
                           freshness_status=current['status'],freshness_reason=current['reason'])
                if age['status']=='DATA_UNAVAILABLE' or current['status']=='DATA_UNAVAILABLE':
                    raise DataError(age['reason'] or current['reason'])
                rows,contracts=normalize_chain(r.raw,request,r.endpoint)
                if age['status']=='STALE' or current['status']=='STALE': raise DataError('STALE_SOURCE')
                for row in rows:
                    row.update(fetch_timestamp=r.received_timestamp,received_timestamp=r.received_timestamp,
                               processing_timestamp=out['processing_timestamp'],data_age_seconds=age['data_age_seconds'],
                               current_age_seconds=current['data_age_seconds'],freshness_status=current['status'])
                out.update(rows=rows,contracts=contracts,coverage_scope='EXACT_RESPONSE_ONLY',contract_universe_complete=False)
            elif kind in ('selector','live_selector'):
                obj=json_value(r.raw)
                values=obj.get('expiresDts' if kind=='selector' else 'expiryDates') if isinstance(obj,dict) else None
                if not isinstance(values,list) or not values: raise DataError('INVALID_EXPIRY_SELECTOR')
                dates=[day(value) for value in values]
                if len(set(dates))!=len(dates) or (kind=='selector' and any(int(d[:4])!=request['year'] for d in dates)):
                    raise DataError('INVALID_EXPIRY_SELECTOR')
                out['rows']=[dict(exchange='NSE',symbol=request['symbol'],instrument=request['instrument_type'],
                    expiry=d,source=SOURCE,source_url=r.endpoint,observed_at=r.received_timestamp,source_hash=digest,
                    status='OBSERVED_NOT_CALENDAR_VERIFIED') for d in dates]
                self.store.cache_observations(out['rows'],provenance)
            else:
                obj=json_value(r.raw)
                if not isinstance(obj,dict) or not isinstance(obj.get('CurrentDay'),list): raise DataError('INVALID_REPORT_LISTING')
                rows=[]
                for group in ('CurrentDay','PreviousDay','FutureDay'):
                    items=obj.get(group,[])
                    if not isinstance(items,list): raise DataError('INVALID_REPORT_LISTING')
                    for item in items:
                        if not isinstance(item,dict) or item.get('fileSegment')!='FO':
                            raise DataError('INVALID_REPORT_LISTING')
                        if any(not isinstance(item.get(k),str) or not item[k] for k in
                               ('fileKey','displayName','fileActlName','filePath','tradingDate')):
                            raise DataError('INVALID_REPORT_LISTING')
                        day(item['tradingDate'])
                        rows.append(dict(item,source=SOURCE))
                out['rows']=rows
                if not out['rows']: raise DataError('EMPTY_DATASET')
            out.update(status='OK',reason=None,row_count=len(out['rows']))
        except (DataError,KeyError,TypeError,ValueError,OSError) as exc:
            out.update(status='DATA_UNAVAILABLE',reason=str(exc) if isinstance(exc,DataError) else 'ACQUISITION_OR_SCHEMA_INVALID',rows=[],contracts=[],row_count=0)
        out['validation_status']=validation_status(out)
        try:self.store.record_validation(out)
        except (DataError,OSError,TypeError,ValueError):
            out.update(status='DATA_UNAVAILABLE',validation_status='DATA_UNAVAILABLE',reason='VALIDATION_LEDGER_WRITE_FAILED',rows=[],contracts=[],row_count=0)
        return out

    def fetch_nse_historical_contract_data(self, *, symbol, instrument_type, trade_date, expiry, option_type, expected_row_count=None):
        request=dict(symbol=symbol,instrument_type=instrument_type,trade_date=trade_date,expiry=expiry,option_type=option_type)
        try:
            request.update(trade_date=day(trade_date),expiry=day(expiry))
            if instrument_type!='OPTIDX' or option_type not in ('CE','PE') or symbol not in {'NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY'}:
                raise DataError('UNVERIFIED_INSTRUMENT_SCHEMA')
        except DataError as exc: return dict(self.result('historical',request),reason=str(exc))
        return self._fetch('historical',request,expected_row_count=expected_row_count)

    def fetch_nse_expiry_selector(self, *, symbol, instrument_type, year=None):
        return self._fetch('selector' if year is not None else 'live_selector',dict(symbol=symbol,instrument_type=instrument_type,year=year))

    def fetch_nse_fno_reports_metadata(self): return self._fetch('reports',{})

    def fetch_nse_bhavcopy_metadata(self, *, trade_date):
        result=self.fetch_nse_fno_reports_metadata()
        if result['status']!='OK': return result
        try:
            rows=[r for r in result['rows'] if r.get('fileKey')=='FO-UDIFF-BHAVCOPY-CSV' and day(r.get('tradingDate'))==day(trade_date)]
        except DataError: rows=[]
        return dict(result,status='SOURCE_METADATA_REQUIRED',validation_status='SOURCE_METADATA_REQUIRED',reason='COMPLETE_DOWNLOAD_URL_NOT_OBSERVED',rows=rows,row_count=len(rows),download_url=None)

    def fetch_nse_option_chain(self, *, symbol, expiry):
        request=dict(symbol=symbol,expiry=expiry,instrument_type='OPTIDX')
        try:request['expiry']=day(expiry)
        except DataError:return self.result('option_chain',request)
        return self._fetch('option_chain',request)

    def resolve_expiry(self, want, **request):
        # Neither an observed expiry list nor a contract subset supplies listing/cutoff history.
        return dict(status='SOURCE_METADATA_REQUIRED',reason='REVIEWED_COMPLETE_CALENDAR_AND_MASTER_REQUIRED',
                    requested_expiry=want,calendar_status='NOT INSTALLED',auto_next='FAIL-CLOSED')
