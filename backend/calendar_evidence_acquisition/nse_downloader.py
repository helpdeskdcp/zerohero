"""Autonomous evidence-only NSE acquisition; no production calendar activation."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import csv
from datetime import date
import fcntl
import io
import json
import logging
import math
import os
from pathlib import Path
import re
import tempfile
import time
import zipfile

from .evidence_engine import EvidenceEngine, now, _json_bytes
from .nse_discovery import PAGE, TITLE, Failure, discover, safe_url, checked_http
from .official_eod import NSE_UDIFF, parse_staged_eod
from .static_archives import digest, MAX_BYTES

LOG = logging.getLogger(__name__)
TARGETS = {'NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY'}
PRICE_FIELDS = {'OpnPric','HghPric','LwPric','ClsPric','LastPric','UndrlygPric','SttlmPric',
                'OpnIntrst','ChngInOpnIntrst','TtlTradgVol','StrkPric'}
REQUIRED = PRICE_FIELDS | {'TradDt','BizDt','Sgmt','Src','FinInstrmTp','FinInstrmId','TckrSymb',
                           'XpryDt','FininstrmActlXpryDt','OptnTp'}


def event(name: str, **fields):
    # Never log request/response headers, cookies or raw browser exceptions.
    LOG.info(json.dumps(dict(event=name, **fields), sort_keys=True))


def validate_archive(raw: bytes, day: date) -> dict:
    if not raw or len(raw)>MAX_BYTES:
        raise Failure('NSE_ARCHIVE_INVALID')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            expected = f'BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv'
            # This is validation of a member name, never construction of a URL.
            if len(members)!=1 or members[0].filename!=expected or members[0].file_size>MAX_BYTES or members[0].flag_bits & 1:
                raise Failure('NSE_ARCHIVE_INVALID')
            payload = archive.read(members[0])  # CRC checked; no extraction to disk.
        reader = csv.DictReader(io.StringIO(payload.decode('utf-8-sig')))
        headers = reader.fieldnames or []
        if not REQUIRED.issubset(headers) or len(headers)!=len(set(headers)):
            raise Failure('NSE_SCHEMA_MISMATCH')
        symbols: set[str] = set()
        count = 0
        expiries: dict[str, set[str]] = {}
        unavailable: dict[str, int] = {}
        for row in reader:
            count += 1
            if None in row or any(v is None for v in row.values()):
                raise Failure('NSE_SCHEMA_MISMATCH')
            if row['TradDt']!=day.isoformat() or row['BizDt']!=day.isoformat():
                raise Failure('NSE_DATE_MISMATCH')
            if row['Src']!='NSE': raise Failure('NSE_EXCHANGE_MISMATCH')
            if row['Sgmt']!='FO': raise Failure('NSE_SEGMENT_MISMATCH')
            if not row['FinInstrmTp'] or not row['FinInstrmId'] or not row['TckrSymb']:
                raise Failure('NSE_SCHEMA_MISMATCH')
            if row['FinInstrmTp'] not in {'IDO','IDF','STO','STF'}:
                raise Failure('NSE_INSTRUMENT_TYPE_INVALID')
            if row['FinInstrmTp'] in {'IDO','STO'} and row['OptnTp'] not in {'CE','PE'}:
                raise Failure('NSE_OPTION_TYPE_INVALID')
            try:
                expiry = date.fromisoformat(row['XpryDt']).isoformat()
                date.fromisoformat(row['FininstrmActlXpryDt'])
            except ValueError as exc: raise Failure('NSE_EXPIRY_INVALID') from exc
            for field in PRICE_FIELDS:
                value = row[field].strip()
                if not value:
                    unavailable[field] = unavailable.get(field,0)+1
                    continue  # Missing is not zero; original bytes retained.
                try: number = float(value)
                except ValueError as exc: raise Failure('NSE_NUMERIC_INVALID') from exc
                if not math.isfinite(number) or (field!='ChngInOpnIntrst' and number<0):
                    raise Failure('NSE_NUMERIC_INVALID')
            symbol = row['TckrSymb']; symbols.add(symbol)
            if symbol in TARGETS:
                expiries.setdefault(symbol,set()).add(expiry)
        if not count or not symbols.intersection(TARGETS):
            raise Failure('NSE_TARGET_INSTRUMENTS_MISSING')
        return dict(trading_date=day.isoformat(), rows=count, members=[expected],
                    target_symbols=sorted(symbols & TARGETS), unavailable_numeric_fields=unavailable,
                    observed_expiries={k:sorted(v) for k,v in sorted(expiries.items())}, sha256=digest(raw))
    except (zipfile.BadZipFile, UnicodeError, csv.Error, RuntimeError, EOFError) as exc:
        raise Failure('NSE_ARCHIVE_INVALID') from exc


def publish(path: Path, data: bytes):
    """Atomic no-clobber publication; existing bytes never overwritten.

    Hard-linking a fully fsynced same-filesystem temporary file is atomic and also
    refuses races with a writer outside our advisory lock, unlike plain rename.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.pending-', delete=False) as stream:
        pending = Path(stream.name)
        try:
            stream.write(data); stream.flush(); os.fsync(stream.fileno())
            try: os.link(pending, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes()!=data:
                    raise Failure('NSE_ARTIFACT_CONFLICT')
            fd=os.open(path.parent,os.O_RDONLY)
            try: os.fsync(fd)
            finally: os.close(fd)
        finally: pending.unlink(missing_ok=True)


class Acquisition:
    def __init__(self, root: Path, transport, *, sleep=time.sleep):
        self.root=Path(root).resolve(); self.transport=transport; self.sleep=sleep

    @contextmanager
    def lock(self):
        folder=self.root/'nse'; folder.mkdir(parents=True,exist_ok=True)
        with (folder/'.acquisition.lock').open('a') as stream:
            try: fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc: raise Failure('NSE_ACQUISITION_BUSY') from exc
            yield

    def retry(self, operation):
        for attempt in range(3):
            try: return operation()
            except Failure as exc:
                if not exc.transient or attempt==2: raise
                event('NSE_RETRY_SCHEDULED',attempt=attempt+1,delay_seconds=2**attempt,reason=exc.reason)
                self.sleep(2**attempt)

    def cached(self, day: date) -> dict | None:
        receipt=self.root/'nse/receipts'/f'{day}.json'
        if not receipt.exists(): return None
        r=json.loads(receipt.read_text())
        path=self.root/r['archive_relative_path']
        if not path.resolve().is_relative_to(self.root/'staging') or path.is_symlink():
            raise Failure('NSE_RECEIPT_INVALID')
        if digest(path.read_bytes())!=r['sha256']: raise Failure('NSE_HASH_MISMATCH')
        validate_archive(path.read_bytes(),day)
        sidecar=path.with_name(path.name+'.json')
        if sidecar.is_symlink(): raise Failure('NSE_RECEIPT_INVALID')
        if digest(sidecar.read_bytes())!=r['sidecar_sha256']: raise Failure('NSE_RECEIPT_INVALID')
        proof_path=self.root/r['proof_relative_path']
        if not proof_path.resolve().is_relative_to(self.root/'nse/proofs') or proof_path.is_symlink():
            raise Failure('NSE_RECEIPT_INVALID')
        if digest(proof_path.read_bytes())!=r['proof_sha256']: raise Failure('NSE_RECEIPT_INVALID')
        proof=json.loads(proof_path.read_text())
        for sha,suffix in [(proof['page_sha256'],'.html')]+[(n['sha256'],'.json') for n in proof.get('network_evidence',[]) if 'sha256' in n]:
            if not re.fullmatch('[0-9a-f]{64}',sha): raise Failure('NSE_RECEIPT_INVALID')
            source=self.root/'nse/proofs'/(sha+suffix)
            if source.is_symlink() or digest(source.read_bytes())!=sha: raise Failure('NSE_RECEIPT_INVALID')
        meta=json.loads(sidecar.read_text())
        if (proof['page_url']!=PAGE or proof['download']['status']!=200 or
                meta['provenance_url']!=proof['download']['url'] or
                meta['provenance_url']!=proof['selected']['download_url'] or
                meta['acquisition_proof_sha256']!=r['proof_sha256'] or
                meta['trading_date']!=str(day) or meta['sha256']!=r['sha256']):
            raise Failure('NSE_RECEIPT_INVALID')
        safe_url(meta['provenance_url'],download=True)
        engine=EvidenceEngine(self.root)
        # Verify the existing ledger and raw/inventory references, without a write.
        ledger=json.loads(engine.ledger.read_text())
        entry=next((e for e in ledger['entries'] if e['entry_id']==r['entry_id']),None)
        if not entry or entry['sha256']!=r['sha256']: raise Failure('NSE_RECEIPT_INVALID')
        engine._verify_entry(entry)
        return dict(status='ALREADY_VERIFIED',verification_scope='ARCHIVE_AND_ACQUISITION_ONLY',
                    calendar_status='UNVERIFIED',archive_path=str(path),sha256=r['sha256'])

    def run(self, requested: date | None, *, dry_run=False, expected_sha256=None) -> dict:
        try:
            if expected_sha256 and not re.fullmatch('[0-9a-f]{64}',expected_sha256):
                raise Failure('EXPECTED_SHA256_INVALID')
            if requested:
                cached=self.cached(requested)
                if cached:
                    if expected_sha256 and cached['sha256']!=expected_sha256: raise Failure('NSE_HASH_MISMATCH')
                    return dict(cached,dry_run=dry_run)
            event('NSE_DISCOVERY_STARTED',date=str(requested) if requested else None)
            def observe():
                proof=self.transport.observe(requested)
                return proof,discover(proof,requested)
            try:
                proof,selected=self.retry(observe)
            except Failure as exc:
                if exc.reason=='SOURCE_METADATA_REQUIRED': raise
                raise Failure('SOURCE_METADATA_REQUIRED',detail=exc.reason) from exc
            day=date.fromisoformat(selected['trading_date'])
            event('NSE_REPORT_FOUND',date=str(day))
            event('NSE_DOWNLOAD_URL_OBSERVED',date=str(day))
            existing=self.existing(day)
            local_hashes=set()
            for path in existing:
                if path.is_symlink() or path.stat().st_size>MAX_BYTES: raise Failure('NSE_ARCHIVE_INVALID')
                raw=path.read_bytes(); validate_archive(raw,day)
                local_hashes.add(digest(raw))
            if len(local_hashes)>1 or (expected_sha256 and local_hashes and local_hashes!={expected_sha256}):
                raise Failure('NSE_HASH_MISMATCH')
            if dry_run:
                return dict(status='DRY_RUN',download_url=selected['download_url'],report_name=TITLE,
                            trading_date=str(day),calendar_status='UNVERIFIED',would_download=not bool(existing),
                            existing_sha256=sorted(local_hashes),
                            existing_archive_requires_source_binding=bool(existing),production_calendar='NOT INSTALLED')
            with self.lock():
                cached=self.cached(day)
                if cached:
                    if expected_sha256 and cached['sha256']!=expected_sha256: raise Failure('NSE_HASH_MISMATCH')
                    return cached
                # An observed link cannot retroactively prove which bytes a past
                # download returned. Preserve unattributed existing bytes unchanged.
                if self.existing(day):
                    raise Failure('SOURCE_METADATA_REQUIRED',detail='EXISTING_ARCHIVE_ORIGIN_UNPROVEN')
                return self.acquire(day,proof,selected,expected_sha256)
        except Failure as exc:
            event(exc.reason,date=str(requested) if requested else None)
            return dict(status='UNVERIFIED',reason=exc.reason,detail=exc.detail,calendar_status='UNVERIFIED',production_calendar='NOT INSTALLED')
        except (OSError,ValueError,KeyError,TypeError) as exc:
            event('NSE_LOCAL_EVIDENCE_ERROR',error_type=type(exc).__name__)
            return dict(status='UNVERIFIED',reason='NSE_LOCAL_EVIDENCE_ERROR',calendar_status='UNVERIFIED',production_calendar='NOT INSTALLED')

    def existing(self, day):
        return [p for p in (self.root/'staging').glob('*.zip')
                if p.name in {f'BhavCopy_NSE_FO_{day:%Y%m%d}.csv.zip',f'BhavCopy_NSE_FO_0_0_0_{day:%Y%m%d}_F_0000.csv.zip'}]

    def acquire(self, day, proof, selected, expected_sha):
        folder=self.root/'nse/pending'; folder.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=folder) as scratch:
            pending=Path(scratch)/'download.zip'
            event('NSE_DOWNLOAD_STARTED',date=str(day))
            def transfer():
                response=self.transport.download(selected,pending)
                checked_http(response['status'])
                return response
            response=self.retry(transfer)
            try:
                checked_http(response['status'])
                safe_url(response['url'],download=True)
                if response['url']!=selected['download_url'] or response.get('redirects'):
                    raise Failure('NSE_REDIRECT_REQUIRES_REVIEW')
                if 'html' in response.get('content_type','').lower(): raise Failure('NSE_ARCHIVE_INVALID')
                if pending.stat().st_size>MAX_BYTES: raise Failure('NSE_ARCHIVE_INVALID')
                raw=pending.read_bytes(); summary=validate_archive(raw,day)
                event('NSE_ARCHIVE_VALIDATED',date=str(day))
                sha=digest(raw)
                if expected_sha and sha!=expected_sha: raise Failure('NSE_HASH_MISMATCH')
                name=response['filename']
                if Path(name).name!=name or name!=summary['members'][0]+'.zip': raise Failure('NSE_FILENAME_INVALID')
                event('NSE_SHA256_VERIFIED',date=str(day),sha256=sha)
                # Existing parser also validates expiry identity before publication.
                parsed=parse_staged_eod(raw,exchange='NSE',segment='FO',trading_date=day,provenance_url=response['url'],fields=NSE_UDIFF)
                if parsed['verification_status']=='CONFLICTING': raise Failure('NSE_CONTRACT_CONFLICT')
                proof_record=dict(page_url=PAGE,page_status=proof['page_status'],
                    observed_at=proof['observed_at'],page_sha256=digest(proof['page_body']),selected=selected,
                    network_evidence=proof.get('network_evidence',[]),download=response)
                proof_bytes=_json_bytes(proof_record); proof_hash=digest(proof_bytes)
                proof_path=self.root/'nse/proofs'/f'{proof_hash}.json'
                publish(self.root/'nse/proofs'/f'{digest(proof["page_body"])}.html',proof['page_body'])
                for item in proof.get('response_bodies',[]):
                    publish(self.root/'nse/proofs'/f'{digest(item)}.json',item)
                publish(proof_path,proof_bytes)
                target=self.root/'staging'/name
                meta=dict(exchange='NSE',segment='FO',trading_date=str(day),provenance_url=response['url'],
                          report_page_url=PAGE,report_name=TITLE,filename=name,sha256=sha,
                          http_status=response['status'],content_type=response.get('content_type'),
                          discovered_at=proof['observed_at'],downloaded_at=response['downloaded_at'],
                          discovery_sha256=digest(proof['page_body']),acquisition_proof_sha256=proof_hash)
                publish(target,raw); publish(Path(str(target)+'.json'),_json_bytes(meta))
                event('NSE_PROVENANCE_RECORDED',date=str(day))
                engine=EvidenceEngine(self.root)
                preview=engine.ingest(target,dry_run=True,acquisition_route='BROWSER')
                if preview['status']=='CONFLICTING' or preview['parser_error']:
                    raise Failure('NSE_EVIDENCE_CONFLICT_OR_REJECTION')
                entry=engine.ingest(target,acquisition_route='BROWSER')
                if entry['reason']!='INDEPENDENT_REVIEW_AND_CALENDAR_COMPLETENESS_REQUIRED' or entry['status']=='CONFLICTING':
                    raise Failure('NSE_EVIDENCE_CONFLICT_OR_REJECTION')
                event('NSE_EVIDENCE_VALIDATED',date=str(day))
                receipt=dict(trading_date=str(day),sha256=sha,archive_relative_path=str(target.relative_to(self.root)),
                             proof_relative_path=str(proof_path.relative_to(self.root)),proof_sha256=proof_hash,
                             sidecar_sha256=digest(_json_bytes(meta)),entry_id=entry['entry_id'],validation=summary,
                             calendar_status='UNVERIFIED')
                publish(self.root/'nse/receipts'/f'{day}.json',_json_bytes(receipt))
                publish(self.root/'nse/inventories'/f'{day}-{sha}.json',_json_bytes(engine.status()))
                event('NSE_CALENDAR_EVIDENCE_ACCEPTED',scope='REVIEW_INVENTORY_ONLY',date=str(day))
                return dict(status='ACQUIRED_FOR_REVIEW',calendar_status='UNVERIFIED',archive_path=str(target),sha256=sha)
            except (Failure,ValueError) as exc:
                if pending.exists():
                    data=pending.read_bytes()
                    publish(self.root/'nse/quarantine'/f'{digest(data)}.zip',data)
                    publish(self.root/'nse/quarantine'/f'{digest(data)}.json',_json_bytes(dict(reason=str(exc),trading_date=str(day))))
                raise


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    choice=parser.add_mutually_exclusive_group(required=True)
    choice.add_argument('--date',type=date.fromisoformat)
    choice.add_argument('--latest-completed',action='store_true')
    parser.add_argument('--dry-run',action='store_true')
    parser.add_argument('--artifacts',type=Path,default=Path('artifacts'))
    parser.add_argument('--expected-sha256')
    args=parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO,format='%(message)s')
    from .nse_browser import NSEBrowser
    transport=NSEBrowser()
    try: result=Acquisition(args.artifacts,transport).run(args.date,dry_run=args.dry_run,expected_sha256=args.expected_sha256)
    finally: transport.close()
    print(json.dumps(result,sort_keys=True))
    return 0 if result['status'] in {'DRY_RUN','ACQUIRED_FOR_REVIEW','ALREADY_VERIFIED'} else 2


if __name__=='__main__':
    raise SystemExit(main())
