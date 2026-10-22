"""Bounded, in-memory table decoding; no network, spreadsheet execution or inference.

XLSX supports a single worksheet with literal cells. Formula cells, external links,
macros, ambiguous tables and unsupported date formats are rejected. Original bytes
are retained by the caller; decoded CSV is only an intermediate representation.
"""
from __future__ import annotations

import csv
from datetime import date, timedelta
import io
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile

from .official_eod import FieldMap, NSE_LEGACY, NSE_UDIFF, NSE_UDIFF_START
from .static_archives import MAX_BYTES, _csv_bytes, _day

NS = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
REL = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}'


def _xml(raw):
    # Reject DTD/entities even if encoded in UTF-16.
    if b'<!DOCTYPE' in raw.replace(b'\x00', b'').upper() or b'<!ENTITY' in raw.replace(b'\x00', b'').upper():
        raise ValueError('XLSX_DTD_UNSUPPORTED')
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError('XLSX_XML_INVALID') from exc


def _xlsx(archive):
    names = archive.namelist()
    if any('externallinks/' in n.lower() or n.lower().endswith('vbaproject.bin') for n in names):
        raise ValueError('XLSX_ACTIVE_CONTENT_UNSUPPORTED')
    workbook = _xml(archive.read('xl/workbook.xml'))
    sheets = workbook.findall(NS + 'sheets/' + NS + 'sheet')
    if len(sheets) != 1:
        raise ValueError('XLSX_SINGLE_WORKSHEET_REQUIRED')
    relations = _xml(archive.read('xl/_rels/workbook.xml.rels'))
    matching = [r for r in relations if r.get('Id') == sheets[0].get(REL + 'id')]
    if len(matching) != 1 or matching[0].get('TargetMode') == 'External':
        raise ValueError('XLSX_WORKSHEET_REFERENCE_INVALID')
    target = matching[0].get('Target', '')
    path = posixpath.normpath(target.lstrip('/') if target.startswith('/') else 'xl/' + target)
    if not path.startswith('xl/worksheets/') or path not in names:
        raise ValueError('XLSX_WORKSHEET_REFERENCE_INVALID')
    strings = []
    if 'xl/sharedStrings.xml' in names:
        strings = [''.join(e.itertext()) for e in _xml(archive.read('xl/sharedStrings.xml'))]
    styles = []
    if 'xl/styles.xml' in names:
        root = _xml(archive.read('xl/styles.xml'))
        custom = {int(f.get('numFmtId')): f.get('formatCode', '').lower()
                  for f in root.findall(NS + 'numFmts/' + NS + 'numFmt')}
        for item in root.findall(NS + 'cellXfs/' + NS + 'xf'):
            number = int(item.get('numFmtId', '0'))
            styles.append(number in {14, 15, 16, 17} or custom.get(number) in
                          {'yyyy-mm-dd', 'dd-mm-yyyy', 'dd-mmm-yyyy', 'dd/mm/yyyy', 'dd-mmm-yy'})
    properties = workbook.find(NS + 'workbookPr')
    epoch1904 = properties is not None and properties.get('date1904') in {'1', 'true'}
    output = io.StringIO()
    writer = csv.writer(output)
    previous_row = 0
    for row in _xml(archive.read(path)).findall(NS + 'sheetData/' + NS + 'row'):
        row_number = int(row.get('r', '0'))
        if row_number <= previous_row:
            raise ValueError('XLSX_ROW_ORDER_INVALID')
        previous_row = row_number
        cells = {}
        for cell in row:
            match = re.fullmatch(r'([A-Z]{1,3})(\d+)', cell.get('r', ''))
            if not match or int(match[2]) != row_number or cell.find(NS + 'f') is not None:
                raise ValueError('XLSX_LITERAL_CELLS_REQUIRED')
            column = 0
            for char in match[1]:
                column = column * 26 + ord(char) - 64
            if column > 512 or column in cells:
                raise ValueError('XLSX_COLUMN_INVALID')
            kind = cell.get('t', 'n')
            value = cell.findtext(NS + 'v', '')
            if kind == 'inlineStr':
                value = ''.join(e.text or '' for e in cell.findall('.//' + NS + 't'))
            elif kind == 's':
                index = int(value)
                if index < 0 or index >= len(strings):
                    raise ValueError('XLSX_STRING_REFERENCE_INVALID')
                value = strings[index]
            elif kind not in {'n', 'str', 'd'}:
                raise ValueError('XLSX_CELL_TYPE_UNSUPPORTED')
            if kind == 'n' and cell.get('s') is not None:
                style = int(cell.get('s'))
                if style < 0 or style >= len(styles):
                    raise ValueError('XLSX_STYLE_INVALID')
                if styles[style] and value:
                    # Date-only integers, with the workbook's explicit date system.
                    if not re.fullmatch(r'\d+', value) or (not epoch1904 and int(value) == 60):
                        raise ValueError('XLSX_DATE_UNSUPPORTED')
                    serial = int(value)
                    if serial > 2958465:
                        raise ValueError('XLSX_DATE_UNSUPPORTED')
                    epoch = date(1904, 1, 1) if epoch1904 else date(1899, 12, 31)
                    value = (epoch + timedelta(days=serial - (1 if not epoch1904 and serial > 60 else 0))).isoformat()
            cells[column] = value
        if cells:
            writer.writerow([cells.get(i, '') for i in range(1, max(cells) + 1)])
        if output.tell() > MAX_BYTES:
            raise ValueError('SOURCE_TOO_LARGE')
    return output.getvalue().encode()


def table_bytes(raw: bytes, *, nested=False) -> bytes:
    """Return CSV bytes from CSV/gzip/ZIP/XLSX without filesystem extraction."""
    if len(raw) > MAX_BYTES:
        raise ValueError('SOURCE_TOO_LARGE')
    if not raw.startswith(b'PK'):
        return _csv_bytes(raw)
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        entries = archive.infolist()
        if len(entries) > 2048 or sum(e.file_size for e in entries) > MAX_BYTES:
            raise ValueError('SOURCE_TOO_LARGE')
        if len({e.filename for e in entries}) != len(entries) or any(e.flag_bits & 1 for e in entries):
            raise ValueError('ARCHIVE_AMBIGUOUS_OR_ENCRYPTED')
        if 'xl/workbook.xml' in archive.namelist():
            return _xlsx(archive)
        tables = [e for e in entries if e.filename.lower().endswith(('.csv', '.xlsx')) and not e.is_dir()]
        if len(tables) != 1 or nested:
            raise ValueError('SINGLE_TABLE_REQUIRED')
        return table_bytes(archive.read(tables[0]), nested=True)


def detect_fields(payload: bytes, exchange: str, trading_date: date) -> FieldMap:
    """Detect unambiguous labeled columns only, never use maturity as expiry."""
    headers = next(csv.reader(io.StringIO(payload.decode('utf-8-sig'))), [])
    if len(headers) != len(set(headers)):
        raise ValueError('DUPLICATE_HEADERS')
    if exchange == 'NSE':
        fields = NSE_UDIFF if trading_date >= NSE_UDIFF_START else NSE_LEGACY
        if any(column not in headers for key, column in vars(fields).items() if key != 'version' and column):
            raise ValueError('NSE_FORMAT_PERIOD_MISMATCH')
        return fields
    aliases = dict(trading_date={'tradingdate', 'tradedate', 'tradDt', 'timestamp', 'date'},
                   underlying={'symbol', 'underlying', 'underlyingsymbol', 'tckrsymb'},
                   instrument={'instrument', 'instrumenttype', 'fininstrmtp'},
                   expiry={'expiry', 'expirydate', 'expirydt', 'expdate', 'xprydt'},
                   maturity={'contractmaturitydate', 'maturitydate'},
                   identifier={'contractid', 'contractidentifier', 'fininstrmid'},
                   actual_expiry={'actualexpirydate', 'fininstrmactlxprydt'},
                   exchange={'exchange', 'src'}, segment={'segment', 'sgmt'},
                   strike={'strikeprice', 'strikepr', 'strkpric'},
                   option_type={'optiontype', 'optiontyp', 'opntp', 'optntp'})
    result = {'version': 'staged-header-detection/v1'}
    for field, names in aliases.items():
        matches = [h for h in headers if re.sub(r'[^a-z0-9]', '', h.lower()) in {n.lower() for n in names}]
        if len(matches) > 1 or (not matches and field in {'trading_date', 'underlying', 'instrument', 'expiry'}):
            raise ValueError('UNAMBIGUOUS_FIELD_MAP_REQUIRED')
        result[field] = matches[0] if matches else None
    return FieldMap(**result)


def trading_date_from_rows(raw: bytes, column: str | None = None) -> date:
    """Establish one literal date from rows, never from expiry or a two-digit year."""
    reader = csv.DictReader(io.StringIO(table_bytes(raw).decode('utf-8-sig')))
    headers = reader.fieldnames or []
    candidates = [h for h in headers if h == column] if column else [h for h in headers
                  if re.sub(r'[^a-z0-9]', '', h.lower()) in {'tradingdate', 'tradedate', 'traddt', 'timestamp', 'date'}]
    if len(candidates) != 1:
        raise ValueError('EXPLICIT_TRADING_DATE_REQUIRED')
    days = {_day(row[candidates[0]]) for row in reader}
    if len(days) != 1:
        raise ValueError('SINGLE_TRADING_DATE_REQUIRED')
    return date.fromisoformat(days.pop())
