import collections,csv,datetime,gzip,hashlib,json,pathlib,shutil,sqlite3,tempfile
ROOT=pathlib.Path('/root/zerohero');BASE=ROOT/'backend/calendar_evidence_acquisition'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def put(name,d):
 p=BASE/name
 if p.exists():raise RuntimeError('Refusing evidence overwrite: '+name)
 p.write_text(json.dumps(d,indent=2,sort_keys=True)+'\n')
def ref(p):return {'path':str(p.relative_to(ROOT)),'sha256':sha(p)}
now=datetime.datetime.now(datetime.timezone.utc).isoformat()
# Evidence aliases only. Never install aliases or calendars in the application.
targets={'NSE':['NIFTY','BANKNIFTY','FINNIFTY','MIDCPNIFTY'],'BSE':['SENSEX','BANKEX'],'MCX':['NATURALGAS','NATGASMINI','CRUDEOIL','CRUDEOILM','GOLD','GOLDM','SILVER','SILVERM']}
raw=BASE/'alternative_sources_20261002/dhan_detailed_master.body';meta=json.loads(raw.with_suffix('.json').read_text())
fields=['EXCH_ID','SEGMENT','UNDERLYING_SYMBOL','INSTRUMENT','SM_EXPIRY_DATE','EXPIRY_FLAG','OPTION_TYPE']
groups=collections.Counter();outside=collections.Counter();n=0
with raw.open() as f:
 reader=csv.DictReader(f);headers=reader.fieldnames
 for r in reader:
  n+=1
  if r['INSTRUMENT'] not in {'OPTIDX','FUTIDX','OPTFUT','FUTCOM'}:continue
  if r['UNDERLYING_SYMBOL'] not in {s for a in targets.values() for s in a}:continue
  if r['UNDERLYING_SYMBOL'] not in targets.get(r['EXCH_ID'],[]):
   outside[(r['EXCH_ID'],r['UNDERLYING_SYMBOL'],r['INSTRUMENT'])]+=1;continue
  groups[tuple(r[k] for k in fields)]+=1
rows=[dict(zip(fields,k),row_count=v) for k,v in sorted(groups.items())]
put('parsed/dhan_master_contract_inventory.json',{'source':ref(raw),'retrieved_at':meta['retrieved_at'],'source_family':'DHAN','provider':'DHAN','exchange':list(targets),'instrument_symbols_as_stored':targets,'evidence_type':'CURRENT_PROVIDER_MASTER_OBSERVATION','headers':headers,'total_master_rows':n,'selected_rows':sum(groups.values()),'groups':rows,'excluded_other_exchange_groups':[dict(exchange=k[0],underlying=k[1],instrument=k[2],row_count=v) for k,v in sorted(outside.items())],'grouping_fields':fields,'observed_expiry_min':min(r['SM_EXPIRY_DATE'] for r in rows),'observed_expiry_max':max(r['SM_EXPIRY_DATE'] for r in rows),'historical_coverage_start':None,'historical_coverage_end':None,'publication_date':None,'listing_times_present':False,'last_trading_time_present':False,'independent_review':None,'limitations':['Retrieval proves bytes observed now, not historical listing/publication, completeness or trading-data availability.','SM_EXPIRY_DATE is a date, not an independently verified cutoff. EXPIRY_FLAG preserved verbatim, never filled from weekday patterns.','Symbols are provider spellings. CRUDEOILM/GOLDM/SILVERM are candidate mini mappings; dated lifecycle mapping still requires review.','All selected expiry dates follow retrieval date; no expired historical contracts found in selected scope.','No current master row authorizes a historical AUTO/NEXT request.']})
# Snapshot the small stable DB without opening a production SQLite connection.
src=ROOT/'backend/data/hist_options.db';before=sha(src)
if pathlib.Path(str(src)+'-wal').exists():raise RuntimeError('WAL present; do not claim consistent main-file snapshot')
with tempfile.TemporaryDirectory() as td:
 cp=pathlib.Path(td)/'snapshot.db';shutil.copyfile(src,cp)
 if sha(src)!=before or sha(cp)!=before:raise RuntimeError('Source changed while copying')
 c=sqlite3.connect('file:'+str(cp)+'?mode=ro&immutable=1',uri=True);c.row_factory=sqlite3.Row
 columns='id,timestamp,timeframe,exchange,underlying,expiry,strike,option_type,security_id,source,greek_inputs'
 records=[dict(r) for r in c.execute('select '+columns+' from hist_option_bars order by id')]
 audit=[dict(r) for r in c.execute('select * from expiry_audit order by id')];c.close()
 rawpath=BASE/'raw/local_rolling_contract_projection.json.gz'
 if rawpath.exists():raise RuntimeError('Refusing overwrite')
 rawpath.write_bytes(gzip.compress(json.dumps({'columns':columns.split(','),'records':records,'expiry_audit':audit},sort_keys=True).encode(),mtime=0))
g=collections.defaultdict(list)
for r in records:g[(r['source'],r['exchange'],r['underlying'],r['expiry'],r['option_type'],r['timeframe'])].append(r)
fields=['source_as_stored','exchange_as_stored','underlying','expiry_label_as_stored','option_type','timeframe']
localgroups=[dict(zip(fields,k),rows=len(v),market_timestamp_min=min(x['timestamp'] for x in v),market_timestamp_max=max(x['timestamp'] for x in v),distinct_strikes=len({x['strike'] for x in v})) for k,v in sorted(g.items())]
put('parsed/local_rolling_contract_inventory.json',{'source_database':{'path':str(src.relative_to(ROOT)),'sha256_at_copy':before,'wal_present':False,'copy_hash_matched_before_and_after':True},'retained_projection':ref(rawpath),'acquired_at':now,'source_family':'LOCAL_RESEARCH_DERIVED_FROM_DHAN_AND_UPSTOX','provider_label':'DHAN_ROLLING','evidence_type':'LOCAL_DERIVED_EXPIRY_LABELS_NOT_PROVIDER_EXPIRY_RESPONSE','groups':localgroups,'row_count':len(records),'audit_rows':len(audit),'audit_source_references':sorted({r['source_reference'] for r in audit if r['source_reference']}),'audit_rule_evidence':sorted({r['rule_evidence'] for r in audit if r['rule_evidence']}),'distinct_expiry_labels':sorted({r['expiry'] for r in records if r['expiry']}),'observation_start':min(r['timestamp'] for r in records),'observation_end':max(r['timestamp'] for r in records),'historical_calendar_coverage_start':None,'historical_calendar_coverage_end':None,'independent_review':None,'limitations':['Projection retains stored identity and provenance, not a newly authenticated Dhan response.','Expiry audit attributes dates to Upstox metadata and BROKER_METADATA_PATTERN; legacy VERIFIED is not Phase 3 verification.','Three observed Tuesday labels seven days apart are not independent proof of a weekly rule or complete calendar; no missing dates filled.','No Dhan security_id, historical publication/listing times or provider-complete universe established.','August market timestamp range proves local stored data presence only; not independent contract or calendar coverage.','Legacy snapshots and raw database left untouched.']})
put('parsed/alternative_acquisition_summary.json',{'version':'2026-10-02.4','acquired_at':now,'network':{'unique_sources_attempted':3,'successful_acquisitions':2,'failed_acquisitions':1,'retry_count':0,'duplicate_requests_sent':0,'duplicate_requests_avoided':4,'avoided_sources':['cached Dhan expired-option docs','cached Dhan expiry-list docs','cached Dhan current F&O calendar','retained AngelOne master'],'redundant_compact_dhan_master_omitted':True,'known_blocked_urls_not_retried':[r['url'] for r in json.loads((BASE/'source_register.json').read_text())['sources'] if r.get('classification')=='ACCESS_BLOCKED']},'new_source_ids':['dhan_instrument_documentation','dhan_detailed_master','nse_historical_contract_report'],'historical_calendar_status_counts':{'VERIFIED':0,'PARTIALLY_VERIFIED':0,'UNVERIFIED':28,'CONFLICTING':0,'NOT_APPLICABLE':0},'production_calendar_installed':False,'production_behavior_changed':False})
print('Retained parsed master:',len(rows),'groups; local projection:',len(records),'rows;',len(audit),'audit rows')
