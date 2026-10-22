"""Offline observations of BSE's acquired standardized contract-master schema.

Schema: EOD File Formats of F&O segment, December v14.2, pages 12–16.
No date generation, network, historical completeness claim or calendar installer.
Hashes bind retained bytes; independent semantic review remains a production gate.
"""
from __future__ import annotations
import csv
from collections import Counter
from decimal import Decimal
import hashlib
import io
import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING
from urllib.parse import urlsplit
import zipfile

from .static_archives import _day, MAX_BYTES
if TYPE_CHECKING:
    from app.nse_official.calendar import OfficialSource

MASTER_URL = 'https://www.bseindia.com/downloads1/Live_Masters_for_Equity_Derivatives.zip'
FORMAT_URL = 'https://www.bseindia.com/downloads1/EOD_FF_and_Sample_Files_BSE_Derivativ_segment.zip'
FORMAT_MEMBER = 'EOD File Formats of F&O segment-December_v.14.2_BSE.pdf'
FORMAT_PDF_SHA256 = '83bf3f010e87e2b3c1ae04947547cef345409c4a216d2847e6f5e734124ab622'
PARSER_VERSION = 'bse-standardized-master/v14.2-observations-1'
REQUIRED = ('FinInstrmId','TckrSymb','FinInstrmNm','XpryDt','StartDt','EndDt',
            'MtrtyDt','StrkPric','OptnTp','MinLot','NewBrdLotQty','PrtdToTrad',
            'DelFlg','CtrctTp','StockNm','DcmlstnPric')
KINDS = {'IO': ('OPTION','OPTIDX'), 'IF': ('FUTURE','FUTIDX')}
CYCLES = {'W':'WEEKLY','M':'MONTHLY','Q':'QUARTERLY','H':'HALF_YEARLY'}
STATES = {'A':'ACTIVE','E':'EXPIRED','X':'MARKED_FOR_EXPIRY'}


def _integer(text: str, *, positive=False) -> int:
    if not re.fullmatch(r'[0-9]+', text):
        raise ValueError('BSE_MASTER_INTEGER_INVALID')
    value = int(text)
    if positive and value == 0:
        raise ValueError('BSE_MASTER_INTEGER_INVALID')
    return value


def parse_master_csv(raw: bytes, *, underlying: str = "SENSEX") -> dict:
    """Parse literal rows for one supported BSE index; never infer another symbol."""
    if underlying not in {"SENSEX", "BANKEX"}:
        raise ValueError("BSE_MASTER_UNDERLYING_UNSUPPORTED")
    if len(raw) > MAX_BYTES:
        raise ValueError('SOURCE_TOO_LARGE')
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig')))
    if (not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames)
            or not set(REQUIRED) <= set(reader.fieldnames)):
        raise ValueError('BSE_MASTER_SCHEMA_MISMATCH')
    contracts, duplicates, conflicts = [], [], []
    seen: dict[str, list[tuple[tuple, int]]] = {}
    total = 0
    for line, row in enumerate(reader, 2):
        total += 1
        if None in row or any(v is None for v in row.values()):
            raise ValueError('BSE_MASTER_ROW_INVALID')
        if row['TckrSymb'] != underlying:
            continue  # No inference from a series name or another symbol.
        try:
            kind, instrument = KINDS[row['FinInstrmNm']]
            cycle, state = CYCLES[row['CtrctTp']], STATES[row['DelFlg']]
        except KeyError as exc:
            raise ValueError('BSE_MASTER_ENUM_UNSUPPORTED') from exc
        if row['PrtdToTrad'] not in ('0','1') or row['DcmlstnPric'] != '2':
            raise ValueError('BSE_MASTER_ENUM_UNSUPPORTED')
        if row['MtrtyDt']:
            # The official v14.2 document declares MtrtyDt a filler, not maturity.
            raise ValueError('BSE_MASTER_UNEXPECTED_FILLER')
        expiry, start, settlement = (_day(row[k]) for k in ('XpryDt','StartDt','EndDt'))
        if start > expiry:
            raise ValueError('BSE_MASTER_INTERVAL_INVALID')
        _integer(row['FinInstrmId'], positive=True)
        lot, minimum_lot = (_integer(row[k],positive=True) for k in ('NewBrdLotQty','MinLot'))
        strike = _integer(row['StrkPric'])
        if (kind == 'OPTION' and row['OptnTp'] not in ('CE','PE')) or (kind == 'FUTURE' and (row['OptnTp'] or strike != 0)):
            raise ValueError('BSE_MASTER_OPTION_IDENTITY_INVALID')
        signature = tuple(sorted(row.items()))
        previous = seen.setdefault(row['FinInstrmId'], [])
        if any(signature == old for old, _ in previous):
            duplicates.append(line)
            continue
        if previous:
            conflicts.append(dict(contract_identifier=row['FinInstrmId'],row_numbers=[previous[0][1],line]))
        previous.append((signature,line))
        contracts.append(dict(exchange='BSE',source='BSE_OFFICIAL',segment='FO',instrument=underlying,
            contract_identifier=row['FinInstrmId'],contract_type=kind,instrument_type=instrument,
            source_instrument_type=row['FinInstrmNm'],expiry=expiry,last_trading_date=expiry,
            settlement_maturity_date=settlement,raw_maturity_filler=row['MtrtyDt'],
            contract_start_date=start,listed_at=None,last_trading_at=None,
            strike=str(Decimal(strike)/100) if kind == 'OPTION' else None,
            strike_raw_paise=row['StrkPric'],option_type=row['OptnTp'] or None,
            lot_size=lot,minimum_lot=minimum_lot,series=row['StockNm'],cycle=cycle,
            contract_state=state,reserved=row['PrtdToTrad']=='1',source_timestamp=None,
            classification='UNVERIFIED',production_eligible=False,row_number=line,
            reasons=['SNAPSHOT_PUBLICATION_TIMESTAMP_UNAVAILABLE','INTRADAY_CUTOFF_UNAVAILABLE',
                     'HISTORICAL_LISTING_COVERAGE_UNPROVEN','INDEPENDENT_PRODUCTION_REVIEW_REQUIRED']))
    return dict(parser_version=PARSER_VERSION,status='CONFLICTING' if conflicts else 'UNVERIFIED',
                source_rows=total,contracts=contracts,duplicates=duplicates,conflicts=conflicts,
                observed_expiries=sorted({r['expiry'] for r in contracts}),
                counts_by_type=dict(Counter(r['contract_type'] for r in contracts)),
                validated_production_expiries=[],complete_historical_calendar=False,
                provider_listing_status='UNVERIFIED',
                production_calendar='NOT_INSTALLED',auto_next='FAIL-CLOSED')


def _member(raw: bytes, name: str) -> bytes:
    if len(raw) > MAX_BYTES:
        raise ValueError('SOURCE_TOO_LARGE')
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ValueError('ARCHIVE_DUPLICATE_MEMBER')
            if any(PurePosixPath(n).is_absolute() or '..' in PurePosixPath(n).parts or '\\' in n for n in names):
                raise ValueError('ARCHIVE_UNSAFE_PATH')
            info = archive.getinfo(name)
            if info.file_size > MAX_BYTES or info.flag_bits & 1:
                raise ValueError('ARCHIVE_MEMBER_UNSUPPORTED')
            return archive.read(name)  # ZIP CRC checked; no filesystem extraction.
    except (zipfile.BadZipFile,KeyError,RuntimeError) as exc:
        raise ValueError('ARCHIVE_INVALID') from exc


def observe_bse_master(master: OfficialSource, discovery: OfficialSource,
                       formats: OfficialSource, *, member: str, underlying: str = "SENSEX") -> dict:
    """Bind parsed observations to acquired official bytes and reviewed file schema.

    Caller retains raw HTTP responses/status. OfficialSource is a trusted curator
    object, not arbitrary client metadata. No browser/network/ingestion is invoked.
    """
    for source in (master,discovery,formats):
        source.validate('BSE')
    if master.url != MASTER_URL or formats.url != FORMAT_URL:
        raise ValueError('BSE_PRODUCTION_MASTER_SOURCE_REQUIRED')
    for source in (master,formats):
        marker = ('["href","'+urlsplit(source.url).path+'"').encode()
        if marker not in discovery.raw:
            raise ValueError('BSE_SOURCE_DISCOVERY_REQUIRED')
    if not re.fullmatch(r'BSE_EQD_CONTRACT_[0-9]{8}\.csv',member):
        raise ValueError('BSE_STANDARDIZED_MEMBER_REQUIRED')
    pdf = _member(formats.raw,FORMAT_MEMBER)
    if hashlib.sha256(pdf).hexdigest() != FORMAT_PDF_SHA256:
        raise ValueError('BSE_FORMAT_VERSION_REVIEW_REQUIRED')
    raw = _member(master.raw,member)
    result = parse_master_csv(raw, underlying=underlying)
    if not result['contracts']:
        raise ValueError('BSE_' + underlying + '_CONTRACTS_UNAVAILABLE')
    provenance = dict(source_url=master.url,sha256=master.sha256,retrieved_at=master.retrieved_at,
        member=member,member_sha256=hashlib.sha256(raw).hexdigest(),
        discovery_url=discovery.url,discovery_sha256=discovery.sha256,
        schema_url=formats.url,schema_archive_sha256=formats.sha256,
        schema_pdf_member=FORMAT_MEMBER,schema_pdf_sha256=FORMAT_PDF_SHA256)
    for row in result['contracts']:
        row.update(classification='OBSERVED_OFFICIAL_EXPIRY',provenance=provenance)
    result['provenance'] = provenance
    # Link/file/row agreement is not independent production lifecycle review.
    return result
