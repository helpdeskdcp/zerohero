"""Angel One SmartAPI market-data client; deliberately no order methods."""
import hashlib, json, os, threading, time
from datetime import datetime, timezone
import requests, pyotp

from .greeks import normalize_greek_row, index_greek_rows, match_greek, merge_leg_greeks

LOGIN = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/"
# NOTE: the Option-Greek API lives under `marketData/v1`, NOT `market/v1`
# (ref: SmartAPI forum topic 4254 "Announcing Option Greeks API").
GREEKS = "https://apiconnect.angelone.in/rest/secure/angelbroking/marketData/v1/optionGreek"
MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
CANDLES = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"

class AngelOneClient:
    def __init__(self, *, cache_path=None, timeout=15):
        self.timeout = timeout; self.cache_path = cache_path or os.getenv("ANGEL_MASTER_CACHE", "/tmp/angelone_instrument_master.json")
        self.jwt = None; self.feed_token = None; self.login_ts = 0; self._master = []
        self.last_auth = {"status": "NOT_ATTEMPTED"}
        # option-greek cache: (UNDERLYING, EXPIRY) -> (expires_at, result-dict).
        # One request per underlying+expiry; a per-key lock collapses concurrent
        # duplicates; OK results live GREEK_TTL_SEC, errors a short negative-cache.
        try:
            self._greek_ttl = max(2.0, float(os.getenv("ANGEL_GREEK_TTL_SEC", "15")))
        except (TypeError, ValueError):
            self._greek_ttl = 15.0
        self._greek_cache: dict[tuple, tuple] = {}
        self._greek_locks: dict[tuple, threading.Lock] = {}
        self._greek_locks_guard = threading.Lock()

    def authenticate(self):
        if self.jwt and time.time() - self.login_ts < 3600: return True
        key, cid, pwd, secret = (os.getenv(k) for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"))
        self.last_auth = {"status": "CONFIG_REQUIRED", "fields": {"api_key": bool(key), "client_id": bool(cid), "password": bool(pwd), "totp_secret": bool(secret)}}
        if not all((key, cid, pwd, secret)): return False
        h = {"Content-Type":"application/json", "Accept":"application/json", "User-Agent":"SmartAPI Python Client",
             "X-PrivateKey":key,
             "X-UserType":"USER", "X-SourceID":"WEB", "X-ClientLocalIP":"127.0.0.1",
             "X-ClientPublicIP":"127.0.0.1", "X-MACAddress":"00:00:00:00:00:00"}
        try:
            r = requests.post(LOGIN, json={"clientcode":cid,"password":pwd,"totp":pyotp.TOTP(secret).now()}, headers=h, timeout=self.timeout)
            d = r.json(); data = d.get("data") or {}
            if d.get("status") and data.get("jwtToken"):
                self.jwt, self.feed_token, self.login_ts = data["jwtToken"], data.get("feedToken"), time.time(); self.last_auth = {"status":"OK"}; return True
            self.last_auth = {"status": "AUTH_FAILED", "errorcode": d.get("errorcode"), "message": str(d.get("message") or d.get("error_type") or "")[:120]}
        except Exception as e:
            self.last_auth = {"status": "AUTH_FAILED", "message": type(e).__name__}
        return False

    def load_instrument_master(self, refresh=False):
        if self._master and not refresh: return self._master
        try:
            if not refresh and os.path.exists(self.cache_path) and time.time()-os.path.getmtime(self.cache_path) < 86400:
                with open(self.cache_path, encoding="utf-8") as f: self._master = json.load(f); return self._master
            r = requests.get(MASTER, timeout=self.timeout); r.raise_for_status(); data = r.json()
            if not isinstance(data, list) or len(data) < 1000: return []
            os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
            tmp = self.cache_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f: json.dump(data, f)
            os.replace(tmp, self.cache_path); self._master = data; return data
        except Exception: return self._master

    @staticmethod
    def _date(v):
        for fmt in ("%d%b%Y", "%Y-%m-%d", "%d-%b-%Y"):
            try: return datetime.strptime(str(v).upper(), fmt).date()
            except ValueError: pass
        return None

    def search_instruments(self, *, symbol=None, exchange=None, instrumenttype=None):
        rows = self.load_instrument_master(); s = {"CRUDEOILMINI":"CRUDEOILM"}.get(str(symbol or "").upper(), str(symbol or "").upper()); e = str(exchange or "").upper()
        return [r for r in rows if (not s or str(r.get("name") or r.get("symbol") or "").upper() == s)
                and (not e or str(r.get("exch_seg") or "").upper() == e)
                and (not instrumenttype or r.get("instrumenttype") == instrumenttype)]

    def search_indices(self, query=""):
        return self.search_instruments(symbol=query or None, exchange="NSE", instrumenttype="AMXIDX")

    def search_equities(self, query=""):
        return [r for r in self.search_instruments(symbol=query or None, exchange="NSE")
                if r.get("instrumenttype") in (None, "EQ")]

    def search_futures(self, query=""):
        return self.search_instruments(symbol=query or None, instrumenttype="FUTCOM")

    def search_options(self, query=""):
        return [r for r in self.search_instruments(symbol=query or None)
                if r.get("instrumenttype") in ("OPTIDX", "OPTSTK", "OPTFUT")]

    def _option_universe(self, underlying):
        """Where this underlying's options live: NSE index/stock (NFO) vs MCX
        commodity options-on-futures (OPTFUT). Returns
        {exchange, types, quote_ex, spot}."""
        u = {"CRUDEOILMINI": "CRUDEOILM"}.get(str(underlying or "").upper(), str(underlying or "").upper())
        mcx = self.search_instruments(symbol=u, exchange="MCX", instrumenttype="OPTFUT")
        if mcx:
            fut = self.resolve_future_contract(u, "AUTO")
            spot = None
            if fut.get("status") == "OK":
                q = self.get_quote("MCX", fut.get("token")) or {}
                spot = q.get("ltp") or q.get("lastTradedPrice")
            return {"exchange": "MCX", "types": ("OPTFUT",), "quote_ex": "MCX", "spot": spot,
                    "fut_expiry": fut.get("expiry") if fut.get("status") == "OK" else None}
        idx = self.resolve_index(u)
        if idx.get("status") == "OK":
            q = self.get_quote("NSE", idx.get("token")) or {}
        else:
            eq = self.resolve_equity(u)
            q = self.get_quote("NSE", eq.get("token")) if eq.get("status") == "OK" else {}
        spot = (q or {}).get("ltp") or (q or {}).get("lastTradedPrice")
        return {"exchange": "NFO", "types": ("OPTIDX", "OPTSTK"), "quote_ex": "NFO", "spot": spot, "fut_expiry": None}

    def resolve_option_contract(self, underlying, expiry="AUTO", strike="ATM", option_type="CE", spot=None):
        u, typ = str(underlying).upper(), str(option_type).upper()
        uni = self._option_universe(u)
        if spot is None:
            spot = uni.get("spot")
        rows = [r for r in self.search_instruments(symbol=u, exchange=uni["exchange"])
                if r.get("instrumenttype") in uni["types"] and str(r.get("symbol", "")).upper().endswith(typ)]
        today = datetime.now(timezone.utc).date(); exps = sorted({r.get("expiry") for r in rows if self._date(r.get("expiry")) and self._date(r.get("expiry")) >= today}, key=self._date)
        if not exps: return {"status":"CONTRACT_INVALID", "reason":"no valid expiry"}
        mode = str(expiry or "AUTO").upper()
        # AUTO_ROLL: like AUTO, but on expiry day itself skip the 0-DTE contract
        # (theta cliff / gamma whipsaw / spread blowout) and take the next one.
        _roll0 = mode == "AUTO_ROLL" and len(exps) > 1 and self._date(exps[0]) == today
        selected = (exps[1] if _roll0 else exps[0]) if mode in ("AUTO", "CURRENT", "AUTO_ROLL") \
            else exps[1] if mode == "NEXT" and len(exps) > 1 \
            else exps[-1] if mode == "LATEST" else expiry
        rows = [r for r in rows if r.get("expiry") == selected]
        if not rows: return {"status":"CONTRACT_INVALID", "reason":"expiry unavailable"}
        def st(r):
            try: return float(r.get("strike"))/100
            except (TypeError, ValueError): return None
        chosen = min(rows, key=lambda r: abs((st(r) or 0)-float(spot))) if strike == "ATM" and spot is not None else next((r for r in rows if st(r) == float(strike)), None)
        if not chosen: return {"status":"CONTRACT_INVALID", "reason":"strike unavailable or spot missing"}
        return {"status":"OK", "exchange":uni["exchange"], "symbol":chosen.get("symbol"), "token":str(chosen.get("token")), "underlying":u, "expiry":selected, "strike":st(chosen), "option_type":typ, "lot_size":chosen.get("lotsize"), "expiry_selection_mode":mode, "available_expiries":exps}

    def resolve_index(self, symbol):
        rows = [r for r in self.search_instruments(symbol=str(symbol).upper(), exchange="NSE") if r.get("instrumenttype") == "AMXIDX"]
        return {"status":"OK", "symbol":str(symbol).upper(), "token":str(rows[0].get("token")), "exchange":"NSE", "underlying":str(symbol).upper()} if rows else {"status":"INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"}

    def resolve_equity(self, symbol):
        rows = [r for r in self.search_instruments(symbol=str(symbol).upper(), exchange="NSE")
                if str(r.get("instrumenttype") or "").upper() in ("EQ", "")]
        if not rows:
            return {"status":"INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"}
        r = rows[0]
        return {"status":"OK", "exchange":"NSE", "symbol":r.get("symbol"),
                "token":str(r.get("token")), "underlying":str(symbol).upper(),
                "instrument_type":r.get("instrumenttype"), "lot_size":r.get("lotsize")}

    def resolve_future_contract(self, symbol, expiry="AUTO"):
        rows=[r for r in self.search_instruments(symbol=symbol, exchange="MCX", instrumenttype="FUTCOM") if r.get("token") and r.get("expiry")]
        today=datetime.now(timezone.utc).date(); rows=[r for r in rows if self._date(r.get("expiry")) and self._date(r.get("expiry"))>=today]
        if not rows: return {"status":"INSTRUMENT_MASTER_CONTRACT_NOT_FOUND"}
        rows.sort(key=lambda r:self._date(r.get("expiry")))
        mode = str(expiry or "AUTO").upper()
        if mode in ("AUTO", "CURRENT"): chosen = rows[0]
        elif mode == "NEXT":
            if len(rows) < 2: return {"status":"CONTRACT_NOT_FOUND", "reason":"next expiry unavailable"}
            chosen = rows[1]
        elif mode == "LATEST": chosen = rows[-1]
        else:
            chosen = next((r for r in rows if str(r.get("expiry")) == str(expiry)), None)
            if chosen is None: return {"status":"CONTRACT_NOT_FOUND", "reason":"expiry unavailable"}
        return {"status":"OK","exchange":"MCX","symbol":chosen.get("symbol"),"token":str(chosen.get("token")),"underlying":str(symbol).upper(),"expiry":chosen.get("expiry"),"lot_size":chosen.get("lotsize"),"expiry_selection_mode":mode}

    @staticmethod
    def _quote_field(data, *keys):
        for k in keys:
            if isinstance(data, dict) and data.get(k) is not None:
                return data[k]
        return None

    @classmethod
    def _quote_leg(cls, token, q):
        """Per-leg fields sourced ONLY from the AngelOne FULL quote. Greeks are
        added later by merge_leg_greeks(); everything here is quote-origin."""
        depth = q.get("depth") if isinstance(q, dict) else None
        bid = ask = None
        if isinstance(depth, dict):
            buy, sell = depth.get("buy") or [], depth.get("sell") or []
            if buy and isinstance(buy[0], dict):
                bid = cls._quote_field(buy[0], "price")
            if sell and isinstance(sell[0], dict):
                ask = cls._quote_field(sell[0], "price")
        return {
            "token": str(token),
            "ltp": cls._quote_field(q, "ltp", "lastTradedPrice"),
            "oi": cls._quote_field(q, "opnInterest", "openInterest"),
            "oi_change": cls._quote_field(q, "changeinOpenInterest", "changeInOpenInterest"),
            "volume": cls._quote_field(q, "tradeVolume", "volume"),
            "bid": bid, "ask": ask, "depth": depth if isinstance(depth, dict) else None,
            "open": cls._quote_field(q, "open"), "high": cls._quote_field(q, "high"),
            "low": cls._quote_field(q, "low"), "close": cls._quote_field(q, "close"),
            "net_change": cls._quote_field(q, "netChange"),
            "pct_change": cls._quote_field(q, "percentChange"),
            "lower_circuit": cls._quote_field(q, "lowerCircuit", "lowerCircuitLimit"),
            "upper_circuit": cls._quote_field(q, "upperCircuit", "upperCircuitLimit"),
            "timestamp": cls._quote_field(q, "exchangeTimestamp", "exchange_timestamp"),
            "quote_status": (q or {}).get("status") if isinstance(q, dict) else "DATA_UNAVAILABLE",
        }

    def get_option_chain(self, underlying, expiry="AUTO", window=5, *, with_greeks=True):
        uni = self._option_universe(underlying); spot = uni.get("spot")
        if spot is None: return {"status":"DATA_UNAVAILABLE", "reason":"spot unavailable"}
        probe = self.resolve_option_contract(underlying, expiry, "ATM", "CE", spot=float(spot))
        if probe.get("status") != "OK": return probe
        selected = probe["expiry"]; rows = [r for r in self.search_instruments(symbol=str(underlying).upper(), exchange=uni["exchange"]) if r.get("expiry") == selected and r.get("instrumenttype") in uni["types"]]
        strikes = sorted({round(float(r.get("strike"))/100, 6) for r in rows if r.get("strike") is not None})
        if not strikes:
            return {"status": "DATA_UNAVAILABLE", "reason": "no strikes in instrument master",
                    "underlying": str(underlying).upper(), "expiry": selected}
        atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i]-float(spot)))
        strikes = strikes[max(0, atm_i-window):atm_i+window+1]

        # ONE greek request for the whole underlying+expiry (cached, deduped).
        greeks = {"status": "SKIPPED", "source": "ANGELONE_OPTION_GREEK",
                  "expiry": selected, "matched": 0, "requested": 0}
        gidx = {}
        if with_greeks:
            g = self.get_option_greeks(str(underlying).upper(), selected)
            gidx = index_greek_rows(g.get("rows") or [])
            greeks = {"status": g.get("status"), "source": g.get("source"),
                      "expiry": selected, "errorcode": g.get("errorcode"),
                      "message": g.get("message"), "cache": g.get("cache"),
                      "rows_returned": len(g.get("rows") or []), "indexed": len(gidx),
                      "matched": 0, "requested": 0}

        out_rows = []
        for strike in strikes:
            legs = {}
            for typ in ("CE", "PE"):
                c = next((r for r in rows if str(r.get("symbol", "")).upper().endswith(typ)
                          and abs(float(r.get("strike"))/100 - strike) < 1e-6), None)
                if not c:
                    continue
                q = self.get_quote(uni["quote_ex"], c.get("token"))
                leg = self._quote_leg(c.get("token"), q)
                leg["strike"] = strike
                leg["option_type"] = typ
                leg["expiry"] = selected
                greeks["requested"] += 1
                grow = match_greek(gidx, strike, typ) if gidx else None
                if grow is not None:
                    greeks["matched"] += 1
                legs[typ] = merge_leg_greeks(leg, grow)
            out_rows.append({"strike": strike, "ce": legs.get("CE"), "pe": legs.get("PE")})

        return {"status": "OK" if out_rows else "DATA_UNAVAILABLE",
                "symbol": str(underlying).upper(), "underlying": str(underlying).upper(),
                "spot": spot, "expiry": selected, "exchange": uni["exchange"],
                "rows": out_rows, "greeks": greeks,
                "timestamp": datetime.now(timezone.utc).isoformat()}

    # ------------------------------------------------------------- option greeks
    def get_option_greeks(self, underlying, expiry):
        """Delta / Gamma / Theta / Vega / IV for every live strike of one
        underlying+expiry, via AngelOne `marketData/v1/optionGreek`.

        ONE broker request per (underlying, expiry); result cached
        `ANGEL_GREEK_TTL_SEC` (default 15s); concurrent duplicate callers share
        the single in-flight request via a per-key lock. Never raises, never
        fabricates — an unavailable field is ``None`` with explicit status.

        Returns (canonical):
          status: OK | NO_DATA | AUTH_FAILED | RATE_LIMITED | TIMEOUT
                  | MALFORMED | API_ERROR
          source: "ANGELONE_OPTION_GREEK" | endpoint | underlying | expiry
          http_status | errorcode | message | fetched_at | cache: HIT|MISS
          rows: [normalize_greek_row(...)]  (empty unless status == OK)
        """
        key = (str(underlying or "").upper(), str(expiry or "").upper())
        now = time.time()
        hit = self._greek_cache.get(key)
        if hit and now < hit[0]:
            return {**hit[1], "cache": "HIT"}
        with self._greek_locks_guard:
            lock = self._greek_locks.setdefault(key, threading.Lock())
        with lock:
            hit = self._greek_cache.get(key)
            if hit and time.time() < hit[0]:
                return {**hit[1], "cache": "HIT"}
            res = self._fetch_option_greeks_uncached(*key)
            ttl = self._greek_ttl if res.get("status") == "OK" else min(5.0, self._greek_ttl)
            self._greek_cache[key] = (time.time() + ttl, res)
            return {**res, "cache": "MISS"}

    def _fetch_option_greeks_uncached(self, name, expirydate):
        base = {"source": "ANGELONE_OPTION_GREEK", "endpoint": GREEKS,
                "underlying": name, "expiry": expirydate,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "http_status": None, "errorcode": "", "message": "", "rows": []}
        if not self.authenticate():
            return {**base, "status": "AUTH_FAILED",
                    "errorcode": (self.last_auth or {}).get("status", "AUTH_FAILED"),
                    "message": "SDK not authenticated"}
        h = {"Content-Type": "application/json", "X-PrivateKey": os.getenv("ANGEL_API_KEY"),
             "Authorization": "Bearer " + self.jwt, "X-UserType": "USER", "X-SourceID": "WEB"}
        try:
            r = requests.post(GREEKS, json={"name": name, "expirydate": expirydate},
                              headers=h, timeout=self.timeout)
        except requests.Timeout:
            return {**base, "status": "TIMEOUT", "errorcode": "TIMEOUT", "message": "request timed out"}
        except Exception as e:
            return {**base, "status": "API_ERROR", "errorcode": type(e).__name__, "message": str(e)[:160]}
        base["http_status"] = r.status_code
        if r.status_code == 429:
            return {**base, "status": "RATE_LIMITED", "errorcode": "RATE_LIMIT", "message": "rate limited"}
        try:
            d = r.json() if r.content else {}
        except Exception:
            return {**base, "status": "MALFORMED", "errorcode": "BAD_JSON", "message": "non-JSON response"}
        errcode = str(d.get("errorcode") or "").strip()
        msg = str(d.get("message") or "").strip()
        data = d.get("data")
        if d.get("status") is True and isinstance(data, list) and data:
            return {**base, "status": "OK", "errorcode": errcode, "message": msg or "SUCCESS",
                    "rows": [normalize_greek_row(x) for x in data]}
        no_data = (errcode.upper() == "AB9019" or "no data" in msg.lower()
                   or (isinstance(data, list) and not data))
        return {**base,
                "status": "NO_DATA" if no_data else "API_ERROR",
                "errorcode": errcode or ("AB9019" if no_data else ""),
                "message": msg or ("no data available" if no_data else "unexpected greek response")}

    def get_greeks(self, symbol, expiry):
        """Deprecated. Kept so old callers keep working; delegates to
        ``get_option_greeks`` and exposes the legacy ``{status, rows}`` shape."""
        res = self.get_option_greeks(symbol, expiry)
        return {"status": "OK" if res.get("status") == "OK" else res.get("status", "API_ERROR"),
                "rows": res.get("rows") or [], "detail": res}

    def get_quote(self, exchange, token):
        if not self.authenticate(): return {"status":"AUTH_FAILED", "data_status":"AUTH_FAILED"}
        key = os.getenv("ANGEL_API_KEY"); h={"Content-Type":"application/json","X-PrivateKey":key,"Authorization":"Bearer "+self.jwt,"X-UserType":"USER","X-SourceID":"WEB"}
        try:
            d=requests.post(QUOTE,json={"mode":"FULL","exchangeTokens":{exchange:[str(token)]}},headers=h,timeout=self.timeout).json(); rows=(d.get("data") or {}).get("fetched") or []
            return {**(rows[0] if rows else {}),"status":"OK" if rows else "DATA_UNAVAILABLE","data_status":"OK" if rows else "DATA_UNAVAILABLE","server_timestamp":datetime.now(timezone.utc).isoformat()}
        except Exception: return {"status":"API_ERROR","data_status":"API_ERROR"}

    def get_quotes(self, exchange, tokens):
        return {str(token): self.get_quote(exchange, token) for token in tokens}

    def get_candles(self, exchange, token, interval="ONE_MINUTE", from_date=None, to_date=None):
        """Read-only historical candles; broker timestamps are preserved."""
        if not self.authenticate():
            return {"status": "AUTH_FAILED", "data_status": "AUTH_FAILED", "candles": []}
        if not from_date or not to_date:
            return {"status": "INVALID_REQUEST", "data_status": "DATA_UNAVAILABLE", "candles": []}
        h = {"Content-Type":"application/json", "X-PrivateKey":os.getenv("ANGEL_API_KEY"),
             "Authorization":"Bearer "+self.jwt, "X-UserType":"USER", "X-SourceID":"WEB"}
        body = {"exchange": str(exchange), "symboltoken": str(token), "interval": str(interval),
                "fromdate": str(from_date), "todate": str(to_date)}
        try:
            d = requests.post(CANDLES, json=body, headers=h, timeout=self.timeout).json()
            raw = d.get("data") or []
            candles = []
            for row in raw:
                if not isinstance(row, (list, tuple)) or len(row) < 6:
                    continue
                candles.append({"timestamp": row[0], "open": row[1], "high": row[2],
                                "low": row[3], "close": row[4], "volume": row[5]})
            return {"status":"OK" if candles else "DATA_UNAVAILABLE",
                    "data_status":"OK" if candles else "DATA_UNAVAILABLE", "candles":candles}
        except Exception:
            return {"status":"API_ERROR", "data_status":"API_ERROR", "candles":[]}

    def resolve_current_expiry(self, underlying):
        return self.resolve_option_contract(underlying, expiry="CURRENT", strike="ATM", option_type="CE")

    def resolve_next_expiry(self, underlying):
        return self.resolve_option_contract(underlying, expiry="NEXT", strike="ATM", option_type="CE")

    def resolve_nearest_expiry(self, underlying):
        return self.resolve_current_expiry(underlying)
