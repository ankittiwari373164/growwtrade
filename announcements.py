"""
BSE corporate-announcements validator (browser-free, Vercel-compatible).

Answers, per symbol: "any BSE filing in the last ~24h, and what was it?"
Used as a CONTEXT flag (a catalyst behind the move), NOT a bull/bear vote --
a filing precedes both rallies and crashes.

BSE blocks naked requests, so we prime cookies from the homepage, then call the
public AnnGetData endpoint with browser-like headers + Referer. Everything is
wrapped so any failure (block, 401, layout change) yields "no catalyst" rather
than breaking the dashboard.

BSE keys announcements by numeric SCRIP CODE, not ticker -- hence SCRIP_MAP.
Extend it as you add symbols (find a code at bseindia.com or via any scrip search).
"""

import datetime as dt
import httpx

# Groww trading symbol -> BSE scrip code. Verify/extend as needed.
SCRIP_MAP = {
    "RELIANCE": "500325", "INFY": "500209", "TCS": "532540", "WIPRO": "507685",
    "HDFCBANK": "500180", "SBIN": "500112", "ITC": "500875", "TATAMOTORS": "500570",
    "TATASTEEL": "500470", "VEDL": "500295", "BEL": "500049", "SUZLON": "532667",
    "YESBANK": "532648", "ETERNAL": "543320",
}

_BASE = "https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
    "Accept-Language": "en-US,en;q=0.9",
}


def _client():
    c = httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True)
    try:
        c.get("https://www.bseindia.com/")            # prime cookies
    except Exception:
        pass
    return c


def get_announcements(symbols, lookback_hours=24):
    """Return {symbol: {"headline": str, "when": str, "recent": bool}} or {}."""
    out = {}
    today = dt.date.today()
    prev = today - dt.timedelta(days=2)
    cutoff = dt.datetime.now() - dt.timedelta(hours=lookback_hours)
    try:
        client = _client()
    except Exception:
        return out

    for sym in symbols:
        code = SCRIP_MAP.get(sym)
        if not code:
            continue
        params = {
            "strCat": "-1", "strPrevDate": prev.strftime("%Y%m%d"),
            "strScrip": code, "strSearch": "P",
            "strToDate": today.strftime("%Y%m%d"), "strType": "C", "pageno": "1",
        }
        try:
            r = client.get(_BASE, params=params)
            r.raise_for_status()
            rows = (r.json() or {}).get("Table", []) or []
            if not rows:
                continue
            top = rows[0]                              # newest first
            head = (top.get("NEWSSUB") or top.get("HEADLINE") or "").strip()
            when = (top.get("NEWS_DT") or top.get("DT_TM") or "").strip()
            recent = False
            try:
                ts = dt.datetime.fromisoformat(when.replace("T", " ").split(".")[0])
                recent = ts >= cutoff
            except Exception:
                recent = True                          # unknown date -> treat as recent
            out[sym] = {"headline": head[:120], "when": when, "recent": recent}
        except Exception:
            continue
    try:
        client.close()
    except Exception:
        pass
    return out
