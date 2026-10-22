"""One bounded unauthenticated GET per new public source; never broker APIs."""
import concurrent.futures,datetime,hashlib,json,pathlib,subprocess
BASE=pathlib.Path('/root/zerohero/backend/calendar_evidence_acquisition')
OUT=BASE/'alternative_sources_20261002';OUT.mkdir(exist_ok=True)
URLS={
 'dhan_instrument_documentation':'https://dhanhq.co/docs/v2/instruments/',
 'dhan_detailed_master':'https://images.dhan.co/api-data/api-scrip-master-detailed.csv',
 'nse_historical_contract_report':'https://www.nseindia.com/report-detail/fo_eq_security',
}
existing=set()
for p in (BASE/'public_sources_20261002').glob('*.json'):
 existing.add(json.loads(p.read_text())['url'])
for p in OUT.glob('*.json'):
 existing.add(json.loads(p.read_text())['url'])
def fetch(item):
 key,url=item
 if url in existing: raise RuntimeError('Refusing duplicate acquisition: '+key)
 body=OUT/(key+'.body');meta=OUT/(key+'.json')
 if body.exists() or meta.exists(): raise RuntimeError('Refusing overwrite: '+key)
 ts=datetime.datetime.now(datetime.timezone.utc).isoformat()
 r=subprocess.run(['curl','-q','--proto','=https','--proto-redir','=https','--location','--max-redirs','2','--connect-timeout','10','--max-time','60','--max-filesize','150000000','--silent','--show-error','--output',str(body),'--write-out','%{http_code}\n%{url_effective}\n%{content_type}\n',url],capture_output=True,text=True)
 lines=r.stdout.splitlines()
 d=dict(source_id=key,url=url,retrieved_at=ts,method='unauthenticated public GET',curl_exit=r.returncode,http_status=lines[0] if lines else None,effective_url=lines[1] if len(lines)>1 else None,content_type=lines[2] if len(lines)>2 else None,error=r.stderr.strip(),path=str(body.relative_to(BASE.parents[1])) if body.exists() else None,sha256=hashlib.sha256(body.read_bytes()).hexdigest() if body.exists() else None,bytes=body.stat().st_size if body.exists() else 0,retry_attempted=False,environment='public document retrieval; curl configuration disabled; no broker API/authentication')
 meta.write_text(json.dumps(d,indent=2)+'\n');return d
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 for d in pool.map(fetch,URLS.items()):print(json.dumps(d),flush=True)
