"""Angel One SmartAPI market-data client; deliberately no order methods."""
import hashlib, json, os, time
from datetime import datetime, timezone
import requests, pyotp

LOGIN = "https://apiconnect.angelone.in/rest/auth/angelbroking/user/v1/loginByPassword"
QUOTE = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/"
MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

class AngelOneClient:
    def __init__(self, *, cache_path=None, timeout=15):
        self.timeout = timeout; self.cache_path = cache_path or os.getenv("ANGEL_MASTER_CACHE", "/tmp/angelone_instrument_master.json")
        self.jwt = None; self.feed_token = None; self.login_ts = 0; self._master = []

    def authenticate(self):
        if self.jwt and time.time() - self.login_ts < 3600: return True
        key, cid, pwd, secret = (os.getenv(k) for k in ("ANGEL_API_KEY", "ANGEL_CLIENT_ID", "ANGEL_PASSWORD", "ANGEL_TOTP_SECRET"))
        if not all((key, cid, pwd, secret)): return False
        h = {"Content-Type":"application/json", "X-PrivateKey":key, "X-UserType":"USER", "X-SourceID":"WEB"}
        try:
            r = requests.post(LOGIN, json={"clientcode":cid,"password":pwd,"totp":pyotp.TOTP(secret).now()}, headers=h, timeout=self.timeout)
            d = r.json(); data = d.get("data") or {}
            if d.get("status") and data.get("jwtToken"):
                self.jwt, self.feed_token, self.login_ts = data["jwtToken"], data.get("feedToken"), time.time(); return True
        except Exception: pass
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
        rows = self.load_instrument_master(); s = str(symbol or "").upper(); e = str(exchange or "").upper()
        return [r for r in rows if (not s or str(r.get("name") or r.get("symbol") or "").upper() == s)
                and (not e or str(r.get("exch_seg") or "").upper() == e)
                and (not instrumenttype or r.get("instrumenttype") == instrumenttype)]

    def resolve_option_contract(self, underlying, expiry="AUTO", strike="ATM", option_type="CE", spot=None):
        u, typ = str(underlying).upper(), str(option_type).upper()
        rows = [r for r in self.search_instruments(symbol=u, exchange="NFO") if r.get("instrumenttype") in ("OPTIDX","OPTSTK") and str(r.get("symbol","")).upper().endswith(typ)]
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
        return {"status":"OK", "exchange":"NFO", "symbol":chosen.get("symbol"), "token":str(chosen.get("token")), "underlying":u, "expiry":selected, "strike":st(chosen), "option_type":typ, "lot_size":chosen.get("lotsize"), "expiry_selection_mode":mode, "available_expiries":exps}

    def get_quote(self, exchange, token):
        if not self.authenticate(): return {"status":"AUTH_FAILED", "data_status":"AUTH_FAILED"}
        key = os.getenv("ANGEL_API_KEY"); h={"Content-Type":"application/json","X-PrivateKey":key,"Authorization":"Bearer "+self.jwt,"X-UserType":"USER","X-SourceID":"WEB"}
        try:
            d=requests.post(QUOTE,json={"mode":"FULL","exchangeTokens":{exchange:[str(token)]}},headers=h,timeout=self.timeout).json(); rows=(d.get("data") or {}).get("fetched") or []
            return {**(rows[0] if rows else {}),"status":"OK" if rows else "DATA_UNAVAILABLE","data_status":"OK" if rows else "DATA_UNAVAILABLE","server_timestamp":datetime.now(timezone.utc).isoformat()}
        except Exception: return {"status":"API_ERROR","data_status":"API_ERROR"}
