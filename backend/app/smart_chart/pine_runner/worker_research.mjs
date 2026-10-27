// RESEARCH COPY of worker.mjs (used only by scripts/pine_nifty_harness.py; the Smart Chart web app does not load this file).
// Difference: a request may carry `base_candles` (1-minute bars) + `base_tf`. The provider then aggregates EVERY timeframe (chart and request.security)
// from the 1-minute bars, so a 3-minute chart can request exact, session-anchored 5-minute bars. Everything else is identical to worker.mjs.
// Smart Chart Pine worker.  One request per process: JSON on stdin -> one JSON line on stdout.
//
// Spawned by app/smart_chart/runner.py with `node --permission` (no fs write, no child
// processes, no worker threads, no native addons), an EMPTY environment, a wall-clock
// timeout and a V8 heap cap.  Network globals are removed below.  The script is Pine
// source that PineTS transpiles; it is never passed to eval() by this file.
//
// Licence note: this file imports `pinets` (AGPL-3.0-only).  See SMART_CHART.md.
import { PineTS, Indicator } from 'pinets';

for (const k of ['fetch', 'WebSocket', 'XMLHttpRequest', 'EventSource', 'Request', 'Response']) {
  try { delete globalThis[k]; } catch { /* ignore */ }
}

const IST_MS = 19_800_000;
const SESSION_OPEN_MS = (9 * 60 + 15) * 60_000; // NSE/BSE cash & index session open, IST
const TF_MS = { '1': 60e3, '3': 180e3, '5': 300e3, '15': 900e3, '30': 1800e3, '60': 3600e3 };

const readStdin = async () => {
  const chunks = [];
  for await (const c of process.stdin) chunks.push(c);
  return Buffer.concat(chunks).toString('utf8');
};

const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);

// ---- timeframe aggregation (same symbol only; derived from the supplied base bars) ----
function tfMs(tf) {
  if (TF_MS[tf]) return TF_MS[tf];
  if (/^\d+$/.test(tf)) return Number(tf) * 60e3;
  return null;
}

function aggregate(bars, tf) {
  if (tf === 'D' || tf === '1D') {
    const out = [];
    let cur = null;
    for (const b of bars) {
      const day = Math.floor((b.openTime + IST_MS) / 86_400_000);
      if (!cur || cur.day !== day) {
        if (cur) out.push(cur.bar);
        cur = { day, bar: { ...b, closeTime: day * 86_400_000 - IST_MS + SESSION_OPEN_MS + 375 * 60_000 } };
      } else {
        const x = cur.bar;
        x.high = Math.max(x.high, b.high); x.low = Math.min(x.low, b.low);
        x.close = b.close; x.volume += b.volume;
      }
    }
    if (cur) out.push(cur.bar);
    return out;
  }
  const ms = tfMs(tf);
  if (!ms) throw Object.assign(new Error(`timeframe "${tf}" is not available (supported: minutes and D)`), { code: 'UNSUPPORTED_TIMEFRAME' });
  const out = [];
  let cur = null;
  for (const b of bars) {
    const dayStart = Math.floor((b.openTime + IST_MS) / 86_400_000) * 86_400_000 - IST_MS;
    const rel = b.openTime - (dayStart + SESSION_OPEN_MS);
    const key = dayStart + SESSION_OPEN_MS + Math.floor(rel / ms) * ms;
    if (!cur || cur.key !== key) {
      if (cur) out.push(cur.bar);
      cur = { key, bar: { ...b, openTime: key, closeTime: key + ms - 1 } };
    } else {
      const x = cur.bar;
      x.high = Math.max(x.high, b.high); x.low = Math.min(x.low, b.low);
      x.close = b.close; x.volume += b.volume;
    }
  }
  if (cur) out.push(cur.bar);
  return out;
}

// Local data source: serves ONLY the chart symbol, derived from the bars the host sent.
// request.security() to another symbol, or to a lower timeframe than the chart, is rejected
// with a clear error instead of silently returning made-up data.
class LocalProvider {
  constructor(symbol, baseTf, bars) { this.symbol = symbol; this.baseTf = baseTf; this.bars = bars; }
  configure() {}
  async getSymbolInfo(tickerId) {
    return { ticker: this.symbol, tickerid: tickerId, description: this.symbol, type: 'index', mintick: 0.05, pricescale: 20, minmove: 1,
      pointvalue: 1, currency: 'INR', timezone: 'Asia/Kolkata', session: '0915-1530', volumetype: 'base', prefix: '', root: this.symbol,
      current_contract: '', isin: '', main_tickerid: tickerId, basecurrency: '', country: 'IN', employees: 0, industry: '', sector: '',
      shareholders: 0, shares_outstanding_float: 0, shares_outstanding_total: 0, expiration_date: 0, mincontract: 1 };
  }
  async getMarketData(tickerId, timeframe, limit, sDate, eDate) {
    const bare = String(tickerId).split(':').pop().split(';')[0];
    if (bare !== this.symbol) {
      throw Object.assign(new Error(`request.security(): symbol "${tickerId}" is not available; only the chart symbol "${this.symbol}" can be requested`), { code: 'UNSUPPORTED_SYMBOL' });
    }
    const want = tfMs(timeframe) ?? (timeframe === 'D' || timeframe === '1D' ? 86_400_000 : null);
    const have = tfMs(this.baseTf) ?? 86_400_000;
    if (want === null) throw Object.assign(new Error(`timeframe "${timeframe}" is not available`), { code: 'UNSUPPORTED_TIMEFRAME' });
    if (want < have) throw Object.assign(new Error(`request.security(): timeframe "${timeframe}" is lower than the chart timeframe "${this.baseTf}"; lower-timeframe data is not available`), { code: 'UNSUPPORTED_TIMEFRAME' });
    let out = want === have ? this.bars : aggregate(this.bars, timeframe);
    if (sDate) out = out.filter((b) => b.openTime >= sDate);
    if (eDate) out = out.filter((b) => b.openTime <= eDate);
    if (limit && out.length > limit) out = out.slice(-limit);
    return out;
  }
}

function toKlines(candles, tf) {
  const ms = tfMs(tf) ?? 86_400_000;
  return candles.map((c) => ({ openTime: c.time, open: c.open, high: c.high, low: c.low, close: c.close, volume: c.volume ?? 0, closeTime: c.time + ms - 1 }));
}

// ---- diagnostics -----------------------------------------------------------------
function lineOf(source, needle) {
  if (!needle) return null;
  const lines = source.split('\n');
  for (let i = 0; i < lines.length; i += 1) {
    const col = lines[i].indexOf(needle);
    if (col >= 0) return { line: i + 1, column: col + 1 };
  }
  return null;
}

function diagnose(err, source) {
  const msg = String(err && err.message ? err.message : err);
  const d = { severity: 'error', code: 'RUNTIME_ERROR', message: msg, line: null, column: null };
  let m = msg.match(/ at (\d+):(\d+)\s*$/);
  if (m) { d.code = 'SYNTAX_ERROR'; d.line = Number(m[1]); d.column = Number(m[2]); return d; }
  m = msg.match(/Undeclared identifier "([^"]+)"/);
  if (m) { d.code = 'UNDECLARED_IDENTIFIER'; Object.assign(d, lineOf(source, m[1]) || {}); return d; }
  m = msg.match(/^([\w.$]+) is not a function/);
  if (m) {
    d.code = 'UNSUPPORTED_FUNCTION';
    d.message = `${m[1]}() is not implemented by the PineTS runtime`;
    Object.assign(d, lineOf(source, m[1]) || {});
    return d;
  }
  if (/Unsupported Pine Script version/.test(msg)) { d.code = 'UNSUPPORTED_VERSION'; return d; }
  if (err && err.code) d.code = err.code;
  return d;
}

// ---- result shaping --------------------------------------------------------------
const STYLE_OF = { line: 'line', shape: 'shape', char: 'char', hline: 'hline', background: 'background', barcolor: 'barcolor' };

function shapePlots(ctx, times) {
  const index = new Map(times.map((t, i) => [t, i]));
  const plots = [];
  const drawings = {};
  for (const [id, p] of Object.entries(ctx.plots || {})) {
    if (id.startsWith('__') && id.endsWith('__')) {
      const items = [];
      for (const d of p.data || []) if (Array.isArray(d.value)) for (const o of d.value) if (!o._deleted) items.push(o);
      drawings[id.slice(2, -2)] = items.slice(-500);
      continue;
    }
    const opts = p.options || {};
    const style = STYLE_OF[opts.style] || (opts.style === 'cross' || opts.style === 'circles' ? 'line' : (opts.style || 'line'));
    const values = new Array(times.length).fill(null);
    const colors = new Array(times.length).fill(null);
    let colorVaries = false;
    for (const d of p.data || []) {
      const i = index.get(d.time);
      if (i === undefined) continue;
      const v = d.value;
      values[i] = typeof v === 'boolean' ? v : num(v);
      const c = d.options && d.options.color;
      if (typeof c === 'string' && c !== 'NaN') { colors[i] = c; if (c !== opts.color) colorVaries = true; }
    }
    plots.push({
      id, title: p.title || id, style, overlay: opts.overlay !== false,
      color: typeof opts.color === 'string' && opts.color !== 'NaN' ? opts.color : null,
      linewidth: opts.linewidth ?? null, location: opts.location ?? null, shape: opts.shape ?? null,
      text: opts.text ?? null, char: opts.char ?? null, values, colors: colorVaries ? colors : null,
    });
  }
  return { plots, drawings };
}

function shapeTrade(t) {
  return {
    id: t.id, entry_id: t.entry_id, exit_id: t.exit_id ?? null, direction: t.size < 0 ? 'SHORT' : 'LONG', size: t.size,
    entry_time: t.entry_time, entry_price: t.entry_price, exit_time: t.exit_time ?? null, exit_price: t.exit_price ?? null,
    entry_bar: t.entry_bar_index, exit_bar: t.exit_bar_index ?? null, commission: num(t.commission) ?? 0, profit: num(t.profit),
    max_drawdown: num(t.max_drawdown), max_runup: num(t.max_runup), status: t.status,
  };
}

function inputsMeta(ind) {
  try {
    return (ind.getInputsMeta() || []).map((m) => ({
      key: m.varId || m.id, id: m.id, name: m.name || m.title || m.varId || m.id, type: m.type, defval: m.defval, group: m.group ?? null,
      options: m.options ?? null, minval: m.minval ?? null, maxval: m.maxval ?? null, step: m.step ?? null, tooltip: m.tooltip ?? null,
    }));
  } catch { return []; }
}

function declKind(source, ind) {
  try { const k = ind.getDeclarationType(); if (k === 'strategy' || k === 'indicator') return k; } catch { /* fall through */ }
  const stripped = source.replace(/\/\/.*$/gm, '');
  if (/^\s*strategy\s*\(/m.test(stripped)) return 'strategy';
  if (/^\s*(indicator|study)\s*\(/m.test(stripped)) return 'indicator';
  return null;
}

function applyInputs(ind, overrides) {
  const errs = [];
  for (const [k, v] of Object.entries(overrides || {})) {
    try { ind.input[k] = v; } catch (e) { errs.push({ severity: 'error', code: 'INVALID_INPUT', message: `input "${k}": ${e.message}`, line: null, column: null }); }
  }
  return errs;
}

const PROP_ALLOW = new Set(['initial_capital', 'commission_type', 'commission_value', 'slippage', 'pyramiding', 'default_qty_type', 'default_qty_value', 'process_orders_on_close', 'margin_long', 'margin_short']);

function applyProps(ind, props) {
  const errs = [];
  for (const [k, v] of Object.entries(props || {})) {
    if (!PROP_ALLOW.has(k)) { errs.push({ severity: 'error', code: 'INVALID_PROP', message: `declaration property "${k}" cannot be overridden`, line: null, column: null }); continue; }
    try { ind.prop[k] = v; } catch (e) { errs.push({ severity: 'error', code: 'INVALID_PROP', message: `property "${k}": ${e.message}`, line: null, column: null }); }
  }
  return errs;
}

async function handle(req) {
  const source = String(req.source || '');
  if (req.op === 'compile') {
    // prepare + a dry run over synthetic bars so unknown functions / identifiers surface as diagnostics
    const ind = new Indicator(source);
    const diagnostics = [];
    let kind = null; let title = null; let meta = [];
    try {
      if (ind.prepare) await ind.prepare();
      meta = inputsMeta(ind); kind = declKind(source, ind);
      const base = 1_700_000_000_000; const bars = [];
      for (let i = 0; i < 60; i += 1) { const p = 100 + Math.sin(i / 5) * 3; bars.push({ openTime: base + i * 300_000, open: p, high: p + 1, low: p - 1, close: p + 0.2, volume: 1000, closeTime: base + (i + 1) * 300_000 - 1 }); }
      const ctx = await new PineTS(new LocalProvider('DRYRUN', '5', bars), 'DRYRUN', '5', 60).run(new Indicator(source));
      title = (ctx.strategy && ctx.strategy.config && ctx.strategy.config.title) || (ctx.indicator && ctx.indicator.title) || null;
      for (const w of ctx.warnings || []) diagnostics.push({ severity: 'warning', code: 'RUNTIME_WARNING', message: String(w.message || JSON.stringify(w)), line: null, column: null });
    } catch (e) { diagnostics.push(diagnose(e, source)); }
    return { ok: !diagnostics.some((d) => d.severity === 'error'), kind, title, inputs: meta, diagnostics };
  }

  if (req.op === 'run') {
    const candles = req.candles || [];
    if (!candles.length) return { ok: false, diagnostics: [{ severity: 'error', code: 'NO_DATA', message: 'no candles supplied', line: null, column: null }] };
    const tf = String(req.tf);
    const ind = new Indicator(source);
    if (ind.prepare) await ind.prepare();
    // PineTS defaults //@version=5 strategies to NO margin model (unlimited leverage); TradingView and the in-chart engine use 100%
    // (an order larger than available equity is rejected). Default to 100% unless the script declares margin itself or the host overrides it,
    // so server backtests/paper runs match what the chart shows.
    const props = { ...(req.props || {}) };
    if (declKind(source, ind) === 'strategy' && !/margin_(long|short)/.test(source.replace(/\/\/.*$/gm, ''))) {
      if (props.margin_long === undefined) props.margin_long = 100;
      if (props.margin_short === undefined) props.margin_short = 100;
    }
    const diagnostics = [...applyInputs(ind, req.inputs), ...applyProps(ind, props)];
    if (diagnostics.length) return { ok: false, diagnostics };
    const symbol = String(req.symbol || 'CHART');
    const baseTf = req.base_tf ? String(req.base_tf) : tf;
    const baseKline = req.base_candles ? toKlines(req.base_candles, baseTf) : null;
    const kline = baseKline ? aggregate(baseKline, tf) : toKlines(candles, tf);
    const ctx = await new PineTS(new LocalProvider(symbol, baseKline ? baseTf : tf, baseKline || kline), symbol, tf, kline.length).run(ind);
    const times = kline.map((k) => k.openTime);
    const kind = declKind(source, ind);
    const shaped = shapePlots(ctx, times);
    const out = {
      ok: true, kind, inputs: inputsMeta(ind), times, ...shaped,
      warnings: (ctx.warnings || []).map((w) => String(w.message || JSON.stringify(w))).slice(0, 50),
      alerts: (ctx.alerts || []).slice(0, 200),
    };
    out.title = (ctx.strategy && ctx.strategy.config && ctx.strategy.config.title) || (ctx.indicator && ctx.indicator.title) || null;
    if (kind === 'strategy' && ctx.strategy) {
      const s = ctx.strategy;
      out.strategy = {
        config: { initial_capital: s.config.initial_capital, commission_type: s.config.commission_type, commission_value: s.config.commission_value,
          slippage: s.config.slippage, pyramiding: s.config.pyramiding, margin_long: s.config.margin_long, margin_short: s.config.margin_short, default_qty_type: s.config.default_qty_type, default_qty_value: s.config.default_qty_value,
          process_orders_on_close: s.config.process_orders_on_close ?? false },
        closed_trades: (s.closedtrades || []).map(shapeTrade),
        open_trades: (s.opentrades || []).map(shapeTrade),
        pine_summary: { netprofit: num(s.netprofit), grossprofit: num(s.grossprofit), grossloss: num(s.grossloss), equity: num(s.equity),
          max_drawdown: num(s.max_drawdown), wintrades: s.wintrades ?? null, losstrades: s.losstrades ?? null },
      };
    }
    return out;
  }
  return { ok: false, diagnostics: [{ severity: 'error', code: 'BAD_OP', message: `unknown op ${req.op}`, line: null, column: null }] };
}

// Write the single result line and exit only once the pipe has flushed: process.exit() right after a large write
// truncates stdout at the 64 KiB pipe buffer.
const finish = (obj) => new Promise((resolve) => process.stdout.write(JSON.stringify(obj) + '\n', resolve));

(async () => {
  let source = '';
  try {
    const req = JSON.parse(await readStdin());
    source = String(req.source || '');
    await finish(await handle(req));
  } catch (e) {
    await finish({ ok: false, diagnostics: [diagnose(e, source)] });
  }
  process.exit(0);
})();
