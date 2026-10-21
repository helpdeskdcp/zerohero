"""Trusted-curator registry of exact retained NSE observations; no URL templates.

Hash checking establishes integrity, not independent review or calendar completeness.
The registry is a repository configuration boundary, never an HTTP request payload.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit, parse_qsl

from .core import DataError, day, instant, json_value, sha, MAX_BYTES


@dataclass(frozen=True)
class Response:
    raw: bytes
    status: int
    received_timestamp: str
    endpoint: str | None
    content_type: str
    sha256: str | None = None
    transport_evidence: dict | None = None
    response_headers: dict | None = None


@dataclass(frozen=True)
class Observation:
    endpoint: str
    parameters: dict
    raw: bytes
    metadata: dict
    kind: str


def official_url(url: str) -> dict:
    p = urlsplit(url)
    if p.scheme != 'https' or p.hostname != 'www.nseindia.com' or p.port not in (None,443) or p.username or p.password or p.fragment:
        raise DataError('OFFICIAL_ENDPOINT_REQUIRED')
    pairs = parse_qsl(p.query, keep_blank_values=True)
    if len({k for k,v in pairs}) != len(pairs): raise DataError('AMBIGUOUS_PARAMETERS')
    if any(any(s in k.lower() for s in ('token','secret','password','auth','cookie','apikey')) for k,v in pairs):
        raise DataError('CREDENTIAL_URL_REJECTED')
    return dict(pairs)


class SourceRegistry:
    def __init__(self, observations: tuple[Observation, ...]): self.observations = observations

    @classmethod
    def from_inventory(cls, path: Path, root: Path):
        root = Path(root).resolve()
        def read(relative):
            target = root/relative
            if target.is_symlink() or not target.resolve().is_relative_to(root) or target.stat().st_size > MAX_BYTES:
                raise DataError('SOURCE_PATH_INVALID')
            return target.read_bytes()
        inventory = json_value(Path(path).read_bytes())
        checked = {}
        for entry in inventory['sources']:
            raw = read(entry['staging_path']); meta = json_value(read(entry['sidecar_path']))
            if sha(raw) != entry['sha256'] or sha(raw) != meta['sha256']: raise DataError('SOURCE_HASH_MISMATCH')
            if (meta.get('source') != 'NSE' or meta.get('independently_observed') is not True
                    or meta.get('observed_download_url') != entry['source_url'] or meta.get('http_status') != 200
                    or meta.get('robots_result') not in ('ALLOWED','POLICY_DOCUMENT_RETRIEVED')
                    or meta.get('redirects_followed') is not False): raise DataError('SOURCE_METADATA_REQUIRED')
            official_url(entry['source_url']); instant(meta['observed_at'])
            if entry['source_url'] in checked: raise DataError('AMBIGUOUS_SOURCE_INVENTORY')
            checked[entry['source_url']] = (raw, meta)
        observations = []
        kinds = {'/api/historicalOR/foCPV':'historical', '/api/historicalOR/meta/foCPV/expireDts':'selector',
                 '/api/daily-reports':'reports', '/api/option-chain-contract-info':'live_selector',
                 '/api/option-chain-v3':'option_chain'}
        for url, (raw, meta) in checked.items():
            for parent in meta.get('discovery_sources',[]):
                if parent['source_url'] not in checked or sha(checked[parent['source_url']][0]) != parent['sha256']:
                    raise DataError('DISCOVERY_CHAIN_INVALID')
            kind = kinds.get(urlsplit(url).path)
            if kind:
                if not meta.get('discovery_sources') or 'json' not in meta['content_type']:
                    raise DataError('DISCOVERY_CHAIN_INVALID')
                if kind=='option_chain':
                    interpretation=meta.get('source_timestamp_interpretation',{})
                    proof=checked.get(interpretation.get('source_url'))
                    if (interpretation.get('timezone')!='Asia/Kolkata' or not proof
                            or sha(proof[0])!=interpretation.get('source_hash')
                            or b'allOcs.timestamp + " IST ' not in proof[0]):
                        raise DataError('SOURCE_TIMEZONE_EVIDENCE_REQUIRED')
                observations.append(Observation(url,official_url(url),raw,meta,kind))
        return cls(tuple(observations))

    def find(self, kind: str, request: dict) -> Observation:
        matches = []
        for obs in self.observations:
            if obs.kind != kind: continue
            p = obs.parameters
            if kind == 'historical':
                match = (p.get('symbol')==request.get('symbol') and p.get('instrumentType')==request.get('instrument_type')
                    and p.get('optionType')==request.get('option_type') and day(p.get('from'))==request.get('trade_date')
                    and day(p.get('to'))==request.get('trade_date') and day(p.get('expiryDate'))==request.get('expiry'))
            elif kind == 'selector':
                match = (p.get('symbol')==request.get('symbol') and p.get('instrument')==request.get('instrument_type')
                         and p.get('year')==str(request.get('year')))
            elif kind=='live_selector':
                match=p=={'symbol':request.get('symbol')} and request.get('instrument_type')=='OPTIDX'
            elif kind=='option_chain':
                match=(p.get('type')=='Indices' and p.get('symbol')==request.get('symbol')
                       and day(p.get('expiry'))==request.get('expiry') and set(p)=={'type','symbol','expiry'})
            else: match = p == {'key':'FO'}
            if match: matches.append(obs)
        if len(matches) != 1: raise DataError('EXACT_OBSERVED_ENDPOINT_REQUIRED')
        return matches[0]
