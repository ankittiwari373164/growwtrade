"""
SQLite logging store for the feedback loop.

Persistence note: this writes a SQLite file under DATA_DIR (default ./data).
It persists on a VM (Oracle) or any host with a persistent disk. On ephemeral
hosts (Vercel serverless, Render free tier) the file resets on restart/redeploy,
so for durable logging run on a persistent-disk host or mount a volume at DATA_DIR.
"""

import os
import json
import sqlite3
import datetime as dt

DATA_DIR = os.environ.get("DATA_DIR", "./data")
_DB = os.path.join(DATA_DIR, "signals.db")


def _conn():
    os.makedirs(DATA_DIR, exist_ok=True)
    c = sqlite3.connect(_DB)
    c.row_factory = sqlite3.Row
    return c


def init():
    try:
        with _conn() as c:
            c.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                date TEXT, symbol TEXT, name TEXT,
                side TEXT, confidence TEXT, leans TEXT,
                entry_price REAL, snap_ts TEXT,
                outcome_ret REAL, mkt_ret REAL, correct INTEGER, outcome_ts TEXT,
                PRIMARY KEY (date, symbol)
            )""")
    except Exception as e:
        print(f"[store] storage unavailable: {e}")


def has_snapshot(date: str) -> bool:
    try:
        with _conn() as c:
            r = c.execute("SELECT 1 FROM signals WHERE date=? LIMIT 1", (date,)).fetchone()
            return r is not None
    except Exception:
        return False


def snapshot(result: dict):
    """Store today's morning signals (once per day). entry_price = best price
    available at snapshot time (Yahoo last, else Groww last)."""
    date = dt.date.today().isoformat()
    if has_snapshot(date):
        return 0
    ts = dt.datetime.now().isoformat(timespec="seconds")
    try:
        rows = []
        # Benchmark row so outcomes can compute market-relative returns.
        # side=neutral means it's never graded as a signal.
        try:
            import analysis
            nifty = analysis.yahoo_closes("^NSEI")
            if nifty:
                rows.append((date, "^NSEI", "NIFTY 50", "neutral", "low",
                             json.dumps({}), nifty[-1], ts))
        except Exception:
            pass
        for s in result.get("stocks", []):
            entry = (s.get("yahoo") or {}).get("last") or (s.get("groww") or {}).get("last")
            leans = {
                "Groww": s.get("groww", {}).get("lean", 0),
                "Yahoo": s.get("yahoo", {}).get("lean", 0),
                "News": s.get("news", {}).get("lean", 0),
                "Window": s.get("window_lean", 0),
            }
            rows.append((date, s["symbol"], s["name"], s["signal"]["side"],
                         s["signal"]["confidence"], json.dumps(leans), entry, ts))
        with _conn() as c:
            c.executemany("""INSERT OR IGNORE INTO signals
                (date,symbol,name,side,confidence,leans,entry_price,snap_ts)
                VALUES (?,?,?,?,?,?,?,?)""", rows)
        return len(rows)
    except Exception:
        return 0


def pending_outcomes(date: str):
    """Rows that have an entry price but no outcome yet."""
    try:
        with _conn() as c:
            return [dict(r) for r in c.execute(
                "SELECT * FROM signals WHERE date=? AND outcome_ret IS NULL "
                "AND entry_price IS NOT NULL", (date,)).fetchall()]
    except Exception:
        return []


def set_outcome(date, symbol, ret, mkt_ret, correct):
    try:
        with _conn() as c:
            c.execute("""UPDATE signals SET outcome_ret=?, mkt_ret=?, correct=?,
                         outcome_ts=? WHERE date=? AND symbol=?""",
                      (ret, mkt_ret, correct, dt.datetime.now().isoformat(timespec="seconds"),
                       date, symbol))
    except Exception:
        pass


def scorecard():
    """Aggregate hit-rates. Only graded rows (correct IS NOT NULL) count."""
    empty = {"overall": {"n": 0, "hits": 0, "rate": None},
             "by_confidence": {"low": {"n": 0, "hits": 0, "rate": None},
                               "medium": {"n": 0, "hits": 0, "rate": None}},
             "by_side": {"bull": {"n": 0, "hits": 0, "rate": None},
                         "bear": {"n": 0, "hits": 0, "rate": None}},
             "per_source": {k: {"n": 0, "rate": None} for k in ("Groww", "Yahoo", "News", "Window")},
             "recent": []}
    try:
        with _conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM signals WHERE correct IS NOT NULL").fetchall()]
    except Exception:
        return empty

    def rate(subset):
        n = len(subset)
        hits = sum(1 for r in subset if r["correct"] == 1)
        return {"n": n, "hits": hits, "rate": round(hits / n * 100, 1) if n else None}

    by_conf = {k: rate([r for r in rows if r["confidence"] == k])
               for k in ("low", "medium")}
    by_side = {k: rate([r for r in rows if r["side"] == k])
               for k in ("bull", "bear")}

    # per-source reliability: when a source leaned, did the day go its way?
    per_source = {}
    for src in ("Groww", "Yahoo", "News", "Window"):
        leaned = []
        for r in rows:
            try:
                lean = json.loads(r["leans"]).get(src, 0)
            except Exception:
                lean = 0
            if lean == 0 or r["outcome_ret"] is None:
                continue
            net = (r["outcome_ret"] or 0) - (r["mkt_ret"] or 0)
            leaned.append(1 if (lean > 0) == (net > 0) else 0)
        n = len(leaned)
        per_source[src] = {"n": n, "rate": round(sum(leaned) / n * 100, 1) if n else None}

    recent = [dict(r) for r in (
        sorted(rows, key=lambda r: (r["date"], r["symbol"]), reverse=True)[:60])]
    return {
        "overall": rate(rows),
        "by_confidence": by_conf,
        "by_side": by_side,
        "per_source": per_source,
        "recent": recent,
    }