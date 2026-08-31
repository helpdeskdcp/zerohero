"""Angel One SmartAPI market-data client; deliberately no order methods."""
import hashlib, json, os, time
from datetime import datetime, timezone
import requests, pyotp

LOGIN = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/"
GREEKS = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/optionGreek"
MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
CANDLES = "https://apiconnect.angelone.in/rest/secure/angelbroking/historical/v1/getCandleData"

class AngelOneClient:
    def __init__(self, *, cache_path=None, timeout=15):
        self.timeout = timeout; self.cache_path = cache_path or os.getenv("ANGEL_MASTER_CACHE", "/tmp/angelone_instrument_master.json")
        self.jwt = None; self.feed_token = None; self.login_ts = 0; self._master = []
        self.last_auth = {"status": "NOT_ATTEMPTED"}

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
        today = datetime.now(timezone.utc).date(); exps = sorted({r.get("expiry") for r in rows if self._date(r.get("expiry")) and self._date(r.get("expiry")) >= today})
        if not exps: return {"status":"CONTRACT_INVALID", "reason":"no valid expiry"}
        mode = str(expiry or "AUTO").upper(); selected = exps[0] if mode in ("AUTO","CURRENT") else exps[1] if mode == "NEXT" and len(exps)>1 else exps[-1] if mode == "LATEST" else expiry
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

    def get_option_chain(self, underlying, expiry="AUTO", window=5):
        uni = self._option_universe(underlying); spot = uni.get("spot")
        if spot is None: return {"status":"DATA_UNAVAILABLE", "reason":"spot unavailable"}
        probe = self.resolve_option_contract(underlying, expiry, "ATM", "CE", spot=float(spot))
        if probe.get("status") != "OK": return probe
        selected = probe["expiry"]; rows = [r for r in self.search_instruments(symbol=str(underlying).upper(), exchange=uni["exchange"]) if r.get("expiry") == selected and r.get("instrumenttype") in uni["types"]]
        strikes = sorted({round(float(r.get("strike"))/100, 6) for r in rows if r.get("strike") is not None})
        atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i]-float(spot)))
        strikes = strikes[max(0, atm_i-window):atm_i+window+1]
        out_rows=[]
        def field(data, *keys):
            for key in keys:
                if data.get(key) is not None:
                    return data[key]
            return None
        for strike in strikes:
            legs={}
            for typ in ("CE","PE"):
                c=next((r for r in rows if str(r.get("symbol","")).upper().endswith(typ) and abs(float(r.get("strike"))/100-strike)<1e-6), None)
                if not c: continue
                q=self.get_quote(uni["quote_ex"], c.get("token")); legs[typ]={"token":str(c.get("token")),"ltp":field(q, "ltp", "lastTradedPrice"),"oi":field(q, "opnInterest", "openInterest"),"oi_change":field(q, "changeinOpenInterest", "changeInOpenInterest"),"volume":field(q, "tradeVolume", "volume"),"timestamp":field(q, "exchangeTimestamp", "exchange_timestamp")}
            out_rows.append({"strike":strike,"ce":legs.get("CE"),"pe":legs.get("PE")})
        return {"status":"OK" if out_rows else "DATA_UNAVAILABLE","symbol":str(underlying).upper(),"underlying":str(underlying).upper(),"spot":spot,"expiry":selected,"exchange":uni["exchange"],"rows":out_rows,"timestamp":datetime.now(timezone.utc).isoformat()}

    def get_greeks(self, symbol, expiry):
        if not self.authenticate(): return {"status":"AUTH_FAILED"}
        h={"Content-Type":"application/json","X-PrivateKey":os.getenv("ANGEL_API_KEY"),"Authorization":"Bearer "+self.jwt,"X-UserType":"USER","X-SourceID":"WEB"}
        try:
            d=requests.post(GREEKS,json={"name":str(symbol).upper(),"expirydate":expiry},headers=h,timeout=self.timeout).json()
            return {"status":"OK" if d.get("status") and d.get("data") else "DATA_UNAVAILABLE","rows":d.get("data") or []}
        except Exception: return {"status":"API_ERROR","rows":[]}

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
