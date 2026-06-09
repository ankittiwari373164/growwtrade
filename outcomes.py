"""
Evening outcome grader. For each signal logged today, compares the day's actual
move against the morning signal -- MARKET-RELATIVE (net of NIFTY), so we measure
the call, not just "did the whole market go up".

Run after market close (~15:45 IST), e.g. via a cron ping to /api/log-outcomes.

Honest definition of "correct" (fixed before looking at results):
  net_return = stock_return - nifty_return
  bull signal correct if net_return > +THRESHOLD
  bear signal correct if net_return < -THRESHOLD
  neutral / conflict signals are NOT graded (excluded from hit-rate).
A move smaller than THRESHOLD counts as a miss (the signal didn't pay).
"""

import datetime as dt
import analysis            # reuse yahoo_closes
import store

THRESHOLD = 0.003          # 0.3% net move required to count as a directional hit
NIFTY = "^NSEI"


def _eod_close(ysym):
    closes = analysis.yahoo_closes(ysym)
    return closes[-1] if closes else None


def run(date: str | None = None):
    date = date or dt.date.today().isoformat()
    pending = store.pending_outcomes(date)
    if not pending:
        return {"date": date, "graded": 0, "note": "nothing pending"}

    mkt_now = _eod_close(NIFTY)
    # market entry stored at snapshot time (benchmark row)
    mkt_entry = next((r["entry_price"] for r in pending if r["symbol"] == NIFTY), None)
    if mkt_entry is None:
        # benchmark may already be graded/absent; fetch from full table
        for r in store.pending_outcomes(date):
            if r["symbol"] == NIFTY:
                mkt_entry = r["entry_price"]
    mkt_ret_day = ((mkt_now - mkt_entry) / mkt_entry) if (mkt_now and mkt_entry) else 0.0

    graded = 0
    for r in pending:
        sym = r["symbol"]
        if sym == NIFTY:
            continue                      # benchmark, not a signal
        # only graded for symbols we can price on Yahoo (mapped names)
        ysym = sym + ".NS" if sym.isupper() and "-" not in sym else None
        if not ysym:
            continue
        close = _eod_close(ysym)
        entry = r["entry_price"]
        if not close or not entry or not mkt_now:
            continue
        ret = (close - entry) / entry
        mkt_ret = mkt_ret_day
        net = ret - mkt_ret
        side = r["side"]
        if side == "bull":
            correct = 1 if net > THRESHOLD else 0
        elif side == "bear":
            correct = 1 if net < -THRESHOLD else 0
        else:
            continue                      # neutral / conflict: not graded
        store.set_outcome(date, sym, round(ret, 5), round(mkt_ret, 5), correct)
        graded += 1
    return {"date": date, "graded": graded, "pending_seen": len(pending)}
