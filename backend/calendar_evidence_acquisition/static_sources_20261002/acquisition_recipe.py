"""Public static sources only. One attempt per new URL; no broker API."""
import concurrent.futures,datetime,hashlib,json,pathlib,subprocess
B=pathlib.Path('/root/zerohero/backend/calendar_evidence_acquisition');O=B/'static_sources_20261002';O.mkdir(exist_ok=True)
URLS={
 'nse_fo_master_20260803':'https://archives.nseindia.com/content/fo/NSE_FO_contract_03082026.csv.gz',
 'nse_udiff_fo_20260803':'https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_20260803_F_0000.csv.zip',
 'jugaad_data_archive_index':'https://api.github.com/repos/jugaad-py/jugaad-data/git/trees/HEAD?recursive=1',
}
import sys
if len(sys.argv)>1: URLS=json.loads(pathlib.Path(sys.argv[1]).read_text())
known={r['url'] for r in json.loads((B/'source_register.json').read_text())['sources']}
known.update(json.loads(p.read_text())['url'] for p in O.glob('*.json'))
def fetch(item):
 key,url=item;body=O/(key+'.body');meta=O/(key+'.json')
 if url in known or meta.exists() or body.exists():raise RuntimeError('Duplicate source refused')
 stamp=datetime.datetime.now(datetime.timezone.utc).isoformat()
 r=subprocess.run(['curl','-q','--proto','=https','--proto-redir','=https','--location','--max-redirs','2','--connect-timeout','8','--max-time','25','--max-filesize','60000000','-sS','-o',str(body),'-w','%{http_code}\n%{url_effective}\n%{content_type}\n',url],capture_output=True,text=True)
 lines=r.stdout.splitlines();d=dict(source_id=key,url=url,retrieved_at=stamp,curl_exit=r.returncode,http_status=lines[0] if lines else None,effective_url=lines[1] if len(lines)>1 else None,content_type=lines[2] if len(lines)>2 else None,error=r.stderr.strip(),path=str(body.relative_to(B.parents[1])) if body.exists() else None,sha256=hashlib.sha256(body.read_bytes()).hexdigest() if body.exists() else None,bytes=body.stat().st_size if body.exists() else 0,retry_attempted=False,method='unauthenticated public static GET')
 meta.write_text(json.dumps(d,indent=2)+'\n');return d
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
 for d in pool.map(fetch,URLS.items()):print(json.dumps(d),flush=True)
