"""
FastAPI server for the dashboard.

Endpoints:
  GET  /            -> dashboard UI
  GET  /api/data    -> analyzed stocks (cached ~2 min)
  POST /api/token   -> paste the morning Groww ACCESS TOKEN (header: X-Secret)
  GET  /api/status  -> token + source status

Security: the Groww access token can place orders. Protect this deployment
(private URL, platform auth) and set DASHBOARD_SECRET so only you can post the
token. The token is held in memory only and dies on restart (re-paste each morning).
"""

import os
import time
import threading

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse

import analysis
import store
import outcomes
import datetime as dt

app = FastAPI(title="Window Signals")
store.init()

_token = os.environ.get("GROWW_ACCESS_TOKEN")   # optional boot value
_cache = {"data": None, "ts": 0}
_lock = threading.Lock()
CACHE_TTL = int(os.environ.get("CACHE_TTL", "120"))
SECRET = os.environ.get("DASHBOARD_SECRET", "")


def get_groww():
    if not _token:
        raise RuntimeError("No Groww access token set. Paste it in the dashboard.")
    from growwapi import GrowwAPI
    return GrowwAPI(_token)


@app.get("/", response_class=HTMLResponse)
def home():
    here = os.path.dirname(__file__)
    return FileResponse(os.path.join(here, "static", "index.html"))


@app.get("/api/status")
def status():
    return {"groww_token_set": bool(_token), "scrape_enabled": analysis.ENABLE_SCRAPE}


@app.post("/api/token")
async def set_token(request: Request):
    if SECRET and request.headers.get("X-Secret") != SECRET:
        raise HTTPException(401, "bad secret")
    body = await request.json()
    tok = (body or {}).get("token", "").strip()
    if not tok:
        raise HTTPException(400, "empty token")
    global _token
    _token = tok
    _cache["ts"] = 0     # invalidate cache so next /api/data uses new token
    return {"ok": True}


@app.get("/api/data")
def data(force: int = 0):
    with _lock:
        fresh = _cache["data"] and (time.time() - _cache["ts"] < CACHE_TTL)
        if fresh and not force:
            return JSONResponse(_cache["data"])
        try:
            result = analysis.analyze(get_groww)
        except Exception as e:
            return JSONResponse({"error": str(e), "stocks": []}, status_code=200)
        _cache["data"], _cache["ts"] = result, time.time()

        # Auto-log the morning snapshot once per day, after the window closes.
        try:
            now = dt.datetime.now().time()
            if now >= analysis.WINDOW_END and not result.get("error"):
                store.snapshot(result)
        except Exception:
            pass
        return JSONResponse(result)


@app.post("/api/log-outcomes")
async def log_outcomes(request: Request, date: str = None):
    if SECRET and request.headers.get("X-Secret") != SECRET:
        raise HTTPException(401, "bad secret")
    return outcomes.run(date)


@app.get("/api/scorecard")
def scorecard_data():
    return JSONResponse(store.scorecard())


@app.get("/scorecard", response_class=HTMLResponse)
def scorecard_page():
    here = os.path.dirname(__file__)
    return FileResponse(os.path.join(here, "static", "scorecard.html"))
