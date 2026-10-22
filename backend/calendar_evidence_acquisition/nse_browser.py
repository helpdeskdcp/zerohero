"""Normal isolated Playwright session for observed public NSE report actions.

No stealth flags, imported cookies, generated API routes or authentication. Unknown
DOM/API layouts fail closed. Date controls must be explicitly labeled/unambiguous.
"""
from __future__ import annotations

import asyncio
from datetime import date
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit

from .evidence_engine import ROBOT_AGENT, robots_policy, BrowserUnavailable, now, identify_filename
from .nse_discovery import PAGE, TITLE, Failure, safe_url, checked_http, json_records, normalized
from .static_archives import MAX_BYTES, digest
from .nse_transport import error_code, diagnostic

# Collect semantic report containers, not positional nth-child selectors. The exact
# label must occur on a leaf/label/link. Neighboring unrelated reports never qualify.
DOM_RECORDS = r'''() => {
  const norm = s => s.replace(/\s+/g, ' ').trim().toLowerCase();
  const title = 'f&o - udiff common bhavcopy final (zip)';
  const rows = [];
  for (const el of document.querySelectorAll('a,label,span,td,div')) {
    if (norm(el.textContent || '') !== title) continue;
    if ([...el.children].some(c => norm(c.textContent || '') === title)) continue;
    const box = el.closest('tr,li,article,[data-report-name]') || el.parentElement;
    if (!box) continue;
    for (const a of box.querySelectorAll('a[href]')) {
      const href = a.getAttribute('href');
      if (!href || /^(javascript:|#)/i.test(href)) continue;
      rows.push({report_name:el.textContent.trim(),download_url:a.href,
        trading_date: a.getAttribute('data-date') || box.getAttribute('data-date') || '', method:'DOM_LINK'});
    }
  }
  return rows;
}'''


class NSEBrowser:
    def __init__(self):
        self.loop=None; self.pw=None; self.browser=None; self.context=None; self.page=None
        self.policies={}; self.tasks=[]; self.records=[]; self.network=[]; self.bodies=[]
        self.capture_day=None; self.capture_event=None; self.blocked=None
        self.http1=False
        self.stage='NOT_STARTED'

    def sync(self, coroutine, seconds):
        if self.loop is None: self.loop=asyncio.new_event_loop()
        try: return self.loop.run_until_complete(asyncio.wait_for(coroutine,seconds))
        except TimeoutError as exc: raise Failure('NSE_TIMEOUT',transient=True) from exc

    def observe(self, requested):
        try:
            return self.sync(self._observe(requested),90)
        except Failure as exc:
            if exc.reason!='NSE_CONNECTION_FAILED' or exc.detail!='ERR_HTTP2_PROTOCOL_ERROR' or self.http1: raise
            diagnostic('NSE_HTTP1_FALLBACK_STARTED',reason=exc.detail)
            self.http1=True
            try:
                return self.sync(self._observe(requested),90)
            except Failure as fallback:
                diagnostic('NSE_HTTP1_FALLBACK_FAILED',stage=self.stage,
                           reason=fallback.reason,code=fallback.detail)
                if fallback.reason in {'NSE_CONNECTION_FAILED','NSE_TIMEOUT'}:
                    # The protocol fallback has already consumed the retry budget.
                    raise Failure('NSE_HTTP1_TRANSPORT_FAILED',detail=fallback.detail or fallback.reason) from fallback
                raise

    def download(self, selected, destination):
        try:
            return self.sync(self._download(selected,destination),60)
        except Failure as exc:
            if self.blocked or exc.reason!='NSE_DOWNLOAD_FAILED' or exc.detail not in {'ERR_HTTP2_PROTOCOL_ERROR','ERR_CONNECTION_RESET','ERR_CONNECTION_CLOSED'}:
                raise
            # A manual URL or matching filename alone is never enough for fallback.
            keys=('download_url','trading_date','method')
            if normalized(str(selected.get('report_name','')))!=normalized(TITLE) or not any(
                all(r.get(k)==selected.get(k) for k in keys)
                and normalized(str(r.get('report_name','')))==normalized(TITLE) for r in self.records
            ):
                raise Failure('SOURCE_METADATA_REQUIRED') from exc
            url=safe_url(selected['download_url'],download=True)
            self.sync(self.policy(url),15)
            from .nse_transport import download_http1
            diagnostic('NSE_DIRECT_HTTP1_FALLBACK_STARTED',host=urlsplit(url).hostname)
            return download_http1(url,Path(destination))

    async def policy(self,url):
        safe_url(url)
        context=self.context
        if context is None: raise Failure('SOURCE_METADATA_REQUIRED')
        parts=urlsplit(url); origin=f'{parts.scheme}://{parts.netloc}'
        if origin not in self.policies:
            # Tasks deduplicate concurrent resources from the same origin.
            async def load():
                response=await context.request.get(origin+'/robots.txt',timeout=10000,max_redirects=0)
                if 500<=response.status<=599: raise Failure('NSE_ROBOTS_UNAVAILABLE',transient=True)
                try: return robots_policy(response.status,await response.text(),'NSE')
                except BrowserUnavailable as exc: raise Failure('NSE_ROBOTS_RESTRICTED') from exc
            self.policies[origin]=asyncio.create_task(load())
        policy=await self.policies[origin]
        if not policy.can_fetch(ROBOT_AGENT,url): raise Failure('NSE_ROBOTS_DISALLOWED')

    async def gate(self,route):
        request=route.request
        try:
            safe_url(request.url)
            if request.method not in {'GET','HEAD'}: raise Failure('NSE_NON_READ_ONLY_REQUEST_BLOCKED')
            await self.policy(request.url)
            if request.redirected_from is not None:
                # No silent URL substitution; all redirects require operator review.
                import logging
                record=dict(event='NSE_REDIRECT_REJECTED',
                            from_host=urlsplit(getattr(request.redirected_from,'url','')).hostname,
                            to_host=urlsplit(request.url).hostname)
                self.network.append(record)
                logging.getLogger(__name__).warning(json.dumps(record))
                raise Failure('NSE_REDIRECT_REQUIRES_REVIEW')
            if self.capture_day is not None and request.resource_type in {'document','xhr','fetch'}:
                safe_url(request.url,download=True)
                self.records.append(dict(report_name=TITLE,trading_date=str(self.capture_day),
                    download_url=request.url,method='BROWSER_REQUEST'))
                self.network.append(dict(url=request.url,method=request.method,
                    relation='EXACT_REPORT_DOWNLOAD_ACTION_REQUEST_ABORTED_BEFORE_BODY'))
                if self.capture_event is None: raise Failure('NSE_CAPTURE_STATE_INVALID')
                self.capture_event.set()
                await route.abort()
                return
        except Failure as exc:
            if request.is_navigation_request(): self.blocked=exc
            await route.abort(); return
        await route.continue_()

    async def collect(self,response):
        try:
            safe_url(response.url)
            headers=await response.all_headers()
            if response.status in {403,429}:
                # A challenge/rate limit on report JSON must not cause retries.
                if response.request.resource_type in {'xhr','fetch'}:
                    self.blocked=Failure('NSE_HTTP_'+str(response.status))
                return
            if response.status!=200 or 'json' not in headers.get('content-type','').lower(): return
            if int(headers.get('content-length','0'))>2*1024*1024: return
            data=await response.body()
            if len(data)>2*1024*1024: return
            records=json_records(json.loads(data),response.url)
            if records:
                self.records.extend(records); self.bodies.append(data)
                self.network.append(dict(url=response.url,http_status=response.status,sha256=digest(data),
                                         relation='REPORT_OBJECT_IN_PAGE_RESPONSE'))
        except (Failure,ValueError,TypeError):
            return

    async def cleanup(self):
        for task in [*self.tasks,*self.policies.values()]:
            if not task.done(): task.cancel()
        if self.tasks or self.policies:
            await asyncio.gather(*self.tasks,*self.policies.values(),return_exceptions=True)
        try:
            if self.browser: await asyncio.wait_for(self.browser.close(),5)
        finally:
            if self.pw: await asyncio.wait_for(self.pw.stop(),5)
            self.browser=self.pw=self.context=self.page=None

    async def _observe(self,requested):
        try:
            from playwright.async_api import async_playwright, Error as PWError, TimeoutError as PWTimeout
        except ImportError as exc: raise Failure('SOURCE_METADATA_REQUIRED',detail='PLAYWRIGHT_NOT_INSTALLED') from exc
        await self.cleanup()
        self.policies={}; self.tasks=[]; self.records=[]; self.network=[]; self.bodies=[]; self.blocked=None
        try:
            self.stage='CHROMIUM_STARTUP'
            self.pw=await async_playwright().start()
            launch_options: dict[str, Any] = dict(headless=True,timeout=20000)
            if self.http1: launch_options['args']=['--disable-http2','--disable-quic']
            self.browser=await self.pw.chromium.launch(**launch_options)
            self.context=await self.browser.new_context(accept_downloads=True,service_workers='block',user_agent=ROBOT_AGENT+'/1')
            await self.context.route('**/*',self.gate)
            self.page=await self.context.new_page(); self.page.set_default_timeout(10000)
            self.page.on('response',lambda response:self.tasks.append(asyncio.create_task(self.collect(response))))
            for url in ('https://www.nseindia.com/',PAGE):
                self.stage='ROBOTS_POLICY'
                await self.policy(url)
                self.stage='REPORT_PAGE' if url==PAGE else 'ROOT_PAGE'
                response=await self.page.goto(url,wait_until='domcontentloaded',timeout=20000)
                if self.blocked: raise self.blocked
                if response is None: raise Failure('NSE_PAGE_UNAVAILABLE',transient=True)
                checked_http(response.status)
                if self.page.url!=url: raise Failure('NSE_REDIRECT_REQUIRES_REVIEW')
            # Wait boundedly for the exact report; never click similar report names.
            label=self.page.get_by_text(re.compile(r'^\s*F&O\s*-\s*UDiFF\s+Common\s+Bhavcopy\s+Final\s*\(zip\)\s*$',re.I)).first
            try: await label.wait_for(state='visible',timeout=10000)
            except PWTimeout: raise Failure('SOURCE_METADATA_REQUIRED',detail='NSE_REPORT_NOT_FOUND')
            form_day=None
            if requested is not None:
                inputs=self.page.locator('input[type="date"]:visible')
                if await inputs.count()==1:
                    await inputs.fill(requested.isoformat()); await inputs.dispatch_event('change')
                    if await inputs.input_value()==requested.isoformat(): form_day=requested
                else:
                    inputs=self.page.get_by_label(re.compile(r'^(?:trading |report |select )?date$',re.I))
                    if await inputs.count()==1:
                        placeholder=(await inputs.get_attribute('placeholder') or '').lower()
                        fmt={'dd-mm-yyyy':'%d-%m-%Y','dd-mmm-yyyy':'%d-%b-%Y','yyyy-mm-dd':'%Y-%m-%d'}.get(placeholder)
                        if fmt and not await inputs.get_attribute('readonly'):
                            value=requested.strftime(fmt)
                            await inputs.fill(value); await inputs.dispatch_event('change')
                            if await inputs.input_value()==value: form_day=requested
            # Read only values actually exposed by DOM and same-page JSON responses.
            await self.page.wait_for_timeout(500)
            if self.tasks: await asyncio.gather(*self.tasks)
            if self.blocked: raise self.blocked
            for record in await self.page.evaluate(DOM_RECORDS):
                if not record['trading_date']:
                    try:
                        venue,day=identify_filename(Path(urlsplit(record['download_url']).path).name)
                        if venue=='NSE': record['trading_date']=str(day)
                    except ValueError:
                        if form_day: record['trading_date']=str(form_day)
                self.records.append(record)
            # For JavaScript-only actions, observe the request triggered by a uniquely
            # scoped Download control, then abort BEFORE downloading (also in dry-run).
            relevant=[r for r in self.records if requested is None or r.get('trading_date')==str(requested)]
            if not relevant and form_day:
                row=label.locator('xpath=ancestor::*[self::tr or self::li or self::article][1]')
                if await row.count()==1:
                    name=re.compile(r'^download$',re.I)
                    action=row.get_by_role('button',name=name).or_(row.get_by_role('link',name=name))
                    if await action.count()==1:
                        self.capture_day=form_day; self.capture_event=asyncio.Event()
                        try:
                            await action.click(timeout=10000)
                            await asyncio.wait_for(self.capture_event.wait(),5)
                        except (PWError,TimeoutError):
                            pass  # No observed request means missing provenance.
                        finally: self.capture_day=None
            return dict(page_url=PAGE,page_status=200,page_body=(await self.page.content()).encode(),
                        observed_at=now(),records=self.records,network_evidence=self.network,response_bodies=self.bodies)
        except Failure: raise
        except PWTimeout as exc:
            diagnostic('NSE_TRANSPORT_TIMEOUT',stage=self.stage,
                       transport='CHROMIUM_HTTP1' if self.http1 else 'CHROMIUM_DEFAULT')
            raise Failure('NSE_TIMEOUT',transient=not self.http1,detail=self.stage) from exc
        except PWError as exc:
            if self.blocked: raise self.blocked
            reason='PLAYWRIGHT_BROWSER_UNAVAILABLE' if self.browser is None else 'NSE_CONNECTION_FAILED'
            code=error_code(exc)
            diagnostic('NSE_TRANSPORT_FAILED',reason=reason,code=code,stage=self.stage,
                       transport='CHROMIUM_HTTP1' if self.http1 else 'CHROMIUM_DEFAULT')
            raise Failure(reason,transient=self.browser is not None and not self.http1 and not code.startswith('ERR_CERT_'),detail=code) from exc

    async def _download(self,selected,destination):
        if self.page is None: raise Failure('SOURCE_METADATA_REQUIRED')
        url=safe_url(selected['download_url'],download=True)
        if not any(r.get('download_url')==url for r in self.records): raise Failure('SOURCE_METADATA_REQUIRED')
        await self.policy(url)
        observed: dict[str, Any] = {}
        redirects=[]
        async def response_seen(response):
            if response.url==url:
                headers=await response.all_headers()
                observed.update(status=response.status,content_type=headers.get('content-type',''))
                if int(headers.get('content-length','0'))>MAX_BYTES: self.blocked=Failure('NSE_ARCHIVE_INVALID')
                if response.status!=200: self.blocked=Failure('NSE_HTTP_'+str(response.status),transient=500<=response.status<=599)
        tasks=[]
        self.page.on('response',lambda response:tasks.append(asyncio.create_task(response_seen(response))))
        try:
            async with self.page.expect_download(timeout=20000) as pending:
                # Navigating to this EXACT observed URL does not manufacture an endpoint.
                await self.page.evaluate('(url) => { window.location.href = url; }',url)
            download=await pending.value
            if download.url!=url:
                redirects.append(download.url)
                raise Failure('NSE_REDIRECT_REQUIRES_REVIEW')
            failure=await download.failure()
            if failure: raise Failure('NSE_DOWNLOAD_FAILED',transient=True,detail=error_code(failure))
            path=await download.path()
            if tasks: await asyncio.gather(*tasks)
            if self.blocked: raise self.blocked
            if not observed: raise Failure('SOURCE_METADATA_REQUIRED',detail='DOWNLOAD_HTTP_RESPONSE_NOT_OBSERVED')
            checked_http(observed['status'])
            if path is None or Path(path).stat().st_size>MAX_BYTES: raise Failure('NSE_ARCHIVE_INVALID')
            await download.save_as(str(destination))
            return dict(url=url,filename=download.suggested_filename,downloaded_at=now(),redirects=redirects,**observed)
        except Failure: raise
        except Exception as exc:
            if self.blocked: raise self.blocked
            raise Failure('NSE_DOWNLOAD_FAILED',transient=True,detail=error_code(exc)) from exc
        finally:
            if tasks: await asyncio.gather(*tasks,return_exceptions=True)

    def close(self):
        if self.loop is not None:
            try: self.sync(self.cleanup(),15)
            except Exception as exc:
                import logging
                logging.getLogger(__name__).warning(json.dumps(dict(event='NSE_BROWSER_CLEANUP_FAILED',error_type=type(exc).__name__)))
            finally: self.loop.close(); self.loop=None
