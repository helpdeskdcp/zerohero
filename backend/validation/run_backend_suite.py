"""Per-file backend validation with unchanged isolated guards and bounded workers."""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
from pathlib import Path
import signal
import subprocess
import xml.etree.ElementTree as ET


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--report-dir', type=Path, required=True)
    args = parser.parse_args()
    root = args.report_dir.resolve()
    if not root.is_relative_to(Path('/tmp')):
        parser.error('reports must be under /tmp')
    root.mkdir(parents=True, exist_ok=True)
    backend = Path(__file__).resolve().parents[1]
    files = sorted((backend/'tests').rglob('test_*.py'))
    def run(path):
        relative = path.relative_to(backend)
        folder = root/relative.with_suffix('')
        folder.mkdir(parents=True, exist_ok=True)
        env = {'PATH':'/usr/bin:/bin','VALIDATION_RUN_DIR':str(folder),'VALIDATION_TEST_TIMEOUT':'60'}
        with (folder/'output.log').open('w') as log:
            p = subprocess.Popen([str(backend/'venv/bin/python'),'-B','validation/isolated_pytest.py',
                                  '-q',str(relative),'--junitxml='+str(folder/'result.xml')],
                                 cwd=backend,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
            try:
                code=p.wait(timeout=180);status='COMPLETED'
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGKILL);p.wait();code=None;status='TIMEOUT'
        return dict(file=str(relative),status=status,exit_code=code,directory=str(folder))
    results=[]
    with ThreadPoolExecutor(max_workers=4) as pool:
        for future in as_completed([pool.submit(run,p) for p in files]):
            results.append(future.result())
            (root/'runs.json').write_text(json.dumps(sorted(results,key=lambda r:r['file']),indent=2))
    totals={k:0 for k in ('tests','failures','errors','skipped')}
    for r in results:
        p=Path(r['directory'])/'result.xml'
        if p.exists():
            for suite in ET.parse(p).getroot().iter('testsuite'):
                for k in totals:totals[k]+=int(suite.get(k,0))
    summary=dict(totals,passed=totals['tests']-totals['failures']-totals['errors']-totals['skipped'],
                 files=len(results),timeouts=sum(r['status']=='TIMEOUT' for r in results),
                 nonzero_files=sum(r['exit_code'] not in (0,None) for r in results))
    (root/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary))
    return int(any(r['exit_code']!=0 for r in results))


if __name__=='__main__':
    raise SystemExit(main())
