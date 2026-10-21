"""Explicit-root immutable evidence store; no existing DB or calendar migration."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import tempfile

from .core import DataError, SOURCE, encoded, json_value, normalize_historical, sha


def publish(path: Path, raw: bytes):
    path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists():
        if path.is_symlink() or path.read_bytes()!=raw: raise DataError('IMMUTABLE_FILE_CONFLICT')
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent,prefix='.pending-')
    try:
        with os.fdopen(fd,'wb') as f: f.write(raw); f.flush(); os.fsync(f.fileno())
        try: os.link(tmp,path)
        except FileExistsError:
            if path.is_symlink() or path.read_bytes()!=raw: raise DataError('IMMUTABLE_FILE_CONFLICT')
        directory=os.open(path.parent,os.O_RDONLY)
        try: os.fsync(directory)
        finally: os.close(directory)
    finally: os.unlink(tmp)


class NSEStore:
    def __init__(self, root: Path): self.root=Path(root).resolve()

    def capture(self, raw: bytes, provenance: dict):
        digest=sha(raw)
        publish(self.root/'raw'/digest,raw)
        receipt=encoded(dict(provenance,sha256=digest))
        publish(self.root/'receipts'/(sha(receipt)+'.json'),receipt)
        return digest

    def record_validation(self, result: dict):
        """Append a complete validation decision, including rejected source bytes' hash."""
        raw=encoded(result)
        publish(self.root/'validations'/(sha(raw)+'.json'),raw)

    def validate_receipt(self, result: dict):
        digest=result['sha256']
        if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
            raise DataError('SHA256_MISMATCH')
        provenance=result['provenance']
        receipt=encoded(dict(provenance,sha256=digest))
        path=self.root/'receipts'/(sha(receipt)+'.json')
        if not path.is_file() or path.is_symlink() or path.read_bytes()!=receipt:
            raise DataError('RECEIPT_MISMATCH')
        if (provenance.get('source')!=SOURCE or provenance.get('endpoint')!=result['endpoint']
                or provenance.get('response_status')!=200
                or provenance.get('received_timestamp')!=result['received_timestamp']
                or provenance.get('request_parameters')!=result['request_parameters']
                or not provenance.get('discovery_sources')):
            raise DataError('RECEIPT_MISMATCH')
        request=result['contract_request']
        if (result.get('exchange')!='NSE' or result.get('data_timestamp')!=request['trade_date']
                or result.get('fetch_timestamp')!=result['received_timestamp']
                or result.get('response_status')!=200 or result.get('source_timestamp') is not None
                or result.get('row_count')!=len(result['rows'])
                or any(result.get(k)!=request[k] for k in ('symbol','instrument_type','expiry','option_type'))):
            raise DataError('DATASET_ENVELOPE_MISMATCH')

    @contextmanager
    def lock(self):
        self.root.mkdir(parents=True,exist_ok=True)
        with (self.root/'.lock').open('a') as stream:
            fcntl.flock(stream,fcntl.LOCK_EX)
            try: yield
            finally: fcntl.flock(stream,fcntl.LOCK_UN)

    def ingest(self, result: dict) -> dict:
        try:
            if result.get('source')!=SOURCE: raise DataError('OFFICIAL_SOURCE_REQUIRED')
            if result.get('status')!='OK' or result.get('dataset_type')!='historical': raise DataError('VALIDATED_HISTORICAL_DATA_REQUIRED')
            self.validate_receipt(result)
            digest=result['sha256']
            if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):
                raise DataError('SHA256_MISMATCH')
            raw=(self.root/'raw'/digest).read_bytes()
            if sha(raw)!=digest: raise DataError('SHA256_MISMATCH')
            rows,contracts=normalize_historical(raw,result['contract_request'],result['endpoint'])
            if rows!=result['rows'] or contracts!=result['contracts']: raise DataError('NORMALIZED_DATA_MISMATCH')
            identity={k:result['contract_request'][k] for k in ('symbol','instrument_type','trade_date','expiry','option_type')}
            identity.update(source=SOURCE,exchange='NSE')
            key=sha(encoded(identity));path=self.root/'datasets'/(key+'.json')
            with self.lock():
                if path.exists():
                    previous=self.read(key)
                    if previous['sha256']!=digest: raise DataError('DATASET_REVISION_CONFLICT')
                    return dict(status='ALREADY_INGESTED',dataset_key=key,rows=len(rows))
                # One atomic dataset publishes its rows, observed master and provenance together.
                publish(path,encoded(dict(result,dataset_key=key,dataset_identity=identity)))
            return dict(status='INGESTED',dataset_key=key,rows=len(rows))
        except (DataError,KeyError,OSError,TypeError) as exc:
            return dict(status='DATA_UNAVAILABLE',reason=str(exc) if isinstance(exc,DataError) else 'STORE_VALIDATION_FAILED')

    def read(self, key: str) -> dict:
        if len(key)!=64 or any(c not in '0123456789abcdef' for c in key): raise DataError('DATASET_KEY_INVALID')
        result=json_value((self.root/'datasets'/(key+'.json')).read_bytes())
        if sha(encoded(result['dataset_identity']))!=key: raise DataError('DATASET_KEY_INVALID')
        identity={k:result['contract_request'][k] for k in ('symbol','instrument_type','trade_date','expiry','option_type')}
        identity.update(source=SOURCE,exchange='NSE')
        if result['dataset_identity']!=identity or result.get('source')!=SOURCE: raise DataError('DATASET_KEY_INVALID')
        self.validate_receipt(result)
        raw=(self.root/'raw'/result['sha256']).read_bytes()
        if sha(raw)!=result['sha256']: raise DataError('SHA256_MISMATCH')
        rows,contracts=normalize_historical(raw,result['contract_request'],result['endpoint'])
        if rows!=result['rows'] or contracts!=result['contracts']: raise DataError('NORMALIZED_DATA_MISMATCH')
        return result

    def cache_observations(self, rows: list[dict], provenance: dict):
        raw=encoded(dict(rows=rows,provenance=provenance,calendar_status='NOT INSTALLED',auto_next='FAIL-CLOSED'))
        publish(self.root/'expiry_observations'/(sha(raw)+'.json'),raw)
