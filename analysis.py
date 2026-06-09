"""
Analysis engine. Universe now comes from Groww's public, server-rendered
"top by volume" page (the active movers you see in-app), scraped browser-free.

Sources cross-checked: Groww API (window turnover + daily indicators),
Yahoo (independent), RSS (news lean), BSE (catalyst flag).

Signals are derived from cross-source AGREEMENT. They are an indicator read,
NOT a prediction of price and NOT financial advice.
"""

import os
import datetime as dt

import httpx
import feedparser
from selectolax.parser import HTMLParser

import announcements

# ---- config ---------------------------------------------------------------
WINDOW_START = dt.time(9, 15)
WINDOW_END = dt.time(9, 25)
TOP_N = int(os.environ.get("TOP_N", "8"))
RSI_PERIOD, SMA_SHORT, SMA_LONG = 14, 20, 50
HISTORY_DAYS = 120
DAILY_INTERVAL_MIN = 1440
ENABLE_SCRAPE = os.environ.get("ENABLE_SCRAPE", "true").lower() == "true"

# Universe source: "top-volume" (most active) or "top-gainers" (biggest jumps).
UNIVERSE_SOURCE = os.environ.get("UNIVERSE_SOURCE", "top-volume")
_SOURCE_URLS = {
    "top-volume":  "https://groww.in/markets/top-volume",
    "top-gainers": "https://groww.in/markets/top-gainers",
    "top-losers":  "https://groww.in/markets/top-losers",
}

# Groww URL slug -> NSE trading symbol. Yahoo symbol is derived as <SYMBOL>.NS.
# Seeded with confident, liquid names. ADD a line for any mover you want full
# Groww/Yahoo data on (find the slug in the stock's Groww URL).
SLUG_MAP = {
    "vodafone-idea-ltd": "IDEA", "zee-entertainment-enterprises-ltd": "ZEEL",
    "yes-bank-ltd": "YESBANK", "jaiprakash-power-ventures-ltd": "JPPOWER",
    "suzlon-energy-ltd": "SUZLON", "ifci-ltd": "IFCI", "reliance-power-ltd": "RPOWER",
    "nhpc-ltd": "NHPC", "canara-bank": "CANBK", "wipro-ltd": "WIPRO",
    "hfcl-ltd": "HFCL", "nmdc-ltd": "NMDC", "tata-steel-ltd": "TATASTEEL",
    "adani-power-ltd": "ADANIPOWER", "idfc-first-bank-ltd": "IDFCFIRSTB",
    "nbcc-india-ltd": "NBCC", "punjab-national-bank": "PNB",
    "bank-of-baroda": "BANKBARODA", "central-bank-of-india": "CENTRALBK",
    "ashok-leyland-ltd": "ASHOKLEY", "hdfc-bank-ltd": "HDFCBANK",
    "state-bank-of-india": "SBIN", "vedanta-ltd": "VEDL",
    "steel-authority-of-india-ltd": "SAIL", "reliance-industries-ltd": "RELIANCE",
    "bharat-heavy-electricals-ltd": "BHEL", "itc-ltd": "ITC",
    "national-aluminium-co-ltd": "NATIONALUM", "bajaj-finance-ltd": "BAJFINANCE",
    "angel-one-ltd": "ANGELONE", "union-bank-of-india": "UNIONBANK",
    "bank-of-maharashtra": "MAHABANK", "bank-of-india": "BANKINDIA",
    "rail-vikas-nigam-ltd": "RVNL", "bharat-electronics-ltd": "BEL",
    "rec-ltd": "RECLTD", "power-finance-corporation-ltd": "PFC",
    "hindustan-zinc-ltd": "HINDZINC", "indian-oil-corporation-ltd": "IOC",
    "ntpc-ltd": "NTPC", "icici-bank-ltd": "ICICIBANK",
    "kotak-mahindra-bank-ltd": "KOTAKBANK", "coal-india-ltd": "COALINDIA",
    "federal-bank-ltd": "FEDERALBNK", "infosys-ltd": "INFY", "idbi-bank-ltd": "IDBI",
    "kalyan-jewellers-india-ltd": "KALYANKJIL",
    "indian-railway-finance-corporation-ltd": "IRFC", "gail-india-ltd": "GAIL",
    "axis-bank-ltd": "AXISBANK", "oil-natural-gas-corporation-ltd": "ONGC",
    "ambuja-cements-ltd": "AMBUJACEM", "zomato-ltd": "ETERNAL",
    "ola-electric-mobility-ltd": "OLAELEC", "inox-wind-ltd": "INOXWIND",
    "samvardhana-motherson-international-ltd": "MOTHERSON",
    "jio-financial-services-ltd": "JIOFIN", "swiggy-ltd": "SWIGGY",
    "himadri-speciality-chemical-ltd": "HSCL",
    "ujjivan-small-finance-bank-ltd": "UJJIVANSFB", "tejas-networks-ltd": "TEJASNET",
    "shipping-corporation-of-india-ltd": "SCI", "sterlite-technologies-ltd": "STLTECH",
    "irb-infrastructure-developers-ltd": "IRB",
    "motherson-sumi-wiring-india-ltd": "MSUMI", "pc-jeweller-ltd": "PCJEWELLER",
    "tata-consultancy-services-ltd": "TCS", "tata-motors-ltd": "TATAMOTORS",
}

# Used only if the scrape is disabled or fails entirely.
HARDCODED_UNIVERSE = [
    {"name": "Reliance Industries", "slug": "reliance-industries-ltd", "symbol": "RELIANCE"},
    {"name": "Infosys", "slug": "infosys-ltd", "symbol": "INFY"},
    {"name": "Tata Consultancy", "slug": "tata-consultancy-services-ltd", "symbol": "TCS"},
    {"name": "Wipro", "slug": "wipro-ltd", "symbol": "WIPRO"},
]

RSS_FEEDS = [
    "https://www.moneycontrol.com/rss/MCtopnews.xml",
    "https://www.livemint.com/rss/markets",
    "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
]
POS = {"surge", "jump", "gain", "rise", "rally", "beat", "upgrade", "profit",
       "high", "soar", "buy", "bullish", "outperform", "record"}
NEG = {"fall", "drop", "decline", "loss", "downgrade", "miss", "slump",
       "plunge", "cut", "bearish", "underperform", "probe", "fraud", "low"}


# ---- math -----------------------------------------------------------------
def sma(a, p): return sum(a[-p:]) / p if len(a) >= p else None
def rsi(c, p):
    if len(c) < p + 1: return None
    g = l = 0.0
    for i in range(len(c) - p, len(c)):
        d = c[i] - c[i - 1]; g += max(d, 0); l += max(-d, 0)
    return 100.0 if l == 0 else 100 - 100 / (1 + (g / p) / (l / p))


def trend_lean(closes):
    if len(closes) < SMA_LONG:
        return 0, "insufficient history"
    s, l, r = sma(closes, SMA_SHORT), sma(closes, SMA_LONG), rsi(closes, RSI_PERIOD)
    lean = 1 if s > l else -1
    if r is not None and r >= 70: lean = min(lean, 0)
    if r is not None and r <= 30: lean = max(lean, 0)
    note = f"SMA{SMA_SHORT}{'>' if s > l else '<'}SMA{SMA_LONG}" + (f", RSI {r:.0f}" if r else "")
    return lean, note


def _slug_to_name(slug):
    base = slug.rsplit("-ltd", 1)[0]
    return " ".join(w.capitalize() for w in base.split("-"))


# ---- universe (browser-free scrape of the active-movers page) -------------
def get_universe():
    if not ENABLE_SCRAPE:
        return list(HARDCODED_UNIVERSE)
    url = _SOURCE_URLS.get(UNIVERSE_SOURCE, _SOURCE_URLS["top-volume"])
    try:
        headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                           "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            "Accept-Language": "en-US,en;q=0.9",
        }
        r = httpx.get(url, headers=headers, timeout=20, follow_redirects=True)
        r.raise_for_status()
        tree = HTMLParser(r.text)
        # Scope to the first list table (the movers list); fall back to whole doc
        # so footer/nav /stocks/ links don't pollute the universe.
        scope = tree.css_first("table.tb10Table") or tree.css_first("table") or tree
        out, seen = [], set()
        for a in scope.css('a[href^="/stocks/"]'):
            href = a.attributes.get("href", "") or ""
            slug = href.rstrip("/").split("/")[-1].lower()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            name = (a.text() or "").strip() or _slug_to_name(slug)
            out.append({"name": name, "slug": slug, "symbol": SLUG_MAP.get(slug)})
        return out or list(HARDCODED_UNIVERSE)
    except Exception:
        return list(HARDCODED_UNIVERSE)


# ---- Groww candles --------------------------------------------------------
def groww_candles(g, sym, start, end, interval):
    if not sym:
        return []
    try:
        r = g.get_historical_candle_data(
            trading_symbol=sym, exchange=g.EXCHANGE_NSE, segment=g.SEGMENT_CASH,
            start_time=start, end_time=end, interval_in_minutes=interval)
        return r.get("candles", []) or []
    except Exception:
        return []


# ---- Yahoo ----------------------------------------------------------------
def yahoo_closes(ysym):
    if not ysym:
        return []
    try:
        r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ysym}",
                      params={"range": "6mo", "interval": "1d"},
                      headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
        r.raise_for_status()
        q = r.json()["chart"]["result"][0]["indicators"]["quote"][0]
        return [x for x in (q.get("close") or []) if x is not None]
    except Exception:
        return []


# ---- RSS news -------------------------------------------------------------
def load_news():
    items = []
    for url in RSS_FEEDS:
        try:
            for e in feedparser.parse(url).entries[:40]:
                items.append((e.get("title", "") + " " + e.get("summary", "")).lower())
        except Exception:
            pass
    return items


def news_lean(name, items):
    key = name.lower().split()[0]
    hits = [t for t in items if key in t]
    if not hits:
        return 0, 0
    score = sum(sum(w in t for w in POS) - sum(w in t for w in NEG) for t in hits)
    return (1 if score > 0 else -1 if score < 0 else 0), len(hits)


# ---- signal ---------------------------------------------------------------
def make_signal(leans: dict):
    active = {k: v for k, v in leans.items() if v != 0}
    bulls = [k for k, v in active.items() if v > 0]
    bears = [k for k, v in active.items() if v < 0]
    if bulls and bears:
        return {"action": "NO TRADE", "side": "conflict", "confidence": "low",
                "detail": f"sources conflict: {', '.join(bulls)} vs {', '.join(bears)}"}
    if not active:
        return {"action": "NEUTRAL", "side": "neutral", "confidence": "low",
                "detail": "no source shows a lean"}
    side = "bull" if bulls else "bear"
    n = len(bulls or bears)
    conf = "medium" if n >= 3 else "low"
    return {"action": "BUY (watch)" if side == "bull" else "SELL (watch)",
            "side": side, "confidence": conf,
            "detail": f"{n}/{len(leans)} sources agree {side}ish"}


# ---- orchestration --------------------------------------------------------
def analyze(get_groww):
    groww, groww_err = None, None
    try:
        groww = get_groww()
    except Exception as e:
        groww_err = str(e)

    universe = get_universe()[: max(TOP_N, 1) + 12]   # a few extra for ranking
    news = load_news()
    now = dt.datetime.now()
    win_s = dt.datetime.combine(now.date(), WINDOW_START).strftime("%Y-%m-%d %H:%M:%S")
    win_e = dt.datetime.combine(now.date(), WINDOW_END).strftime("%Y-%m-%d %H:%M:%S")
    hist_s = (now - dt.timedelta(days=HISTORY_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    hist_e = now.strftime("%Y-%m-%d %H:%M:%S")

    # Page order already = volume rank. Refine with Groww window turnover when
    # the symbol is known; otherwise keep page order.
    for i, u in enumerate(universe):
        u["page_rank"] = i
        u["turnover"] = u["win_move"] = None
        if groww and u["symbol"]:
            cs = groww_candles(groww, u["symbol"], win_s, win_e, 1)
            if cs:
                u["turnover"] = sum(c[4] * c[5] for c in cs)
                u["win_move"] = (cs[-1][4] - cs[0][1]) / cs[0][1] * 100
    if any(u["turnover"] for u in universe):
        universe.sort(key=lambda u: (u["turnover"] or -1), reverse=True)
    top = universe[:TOP_N]

    anns = announcements.get_announcements([u["symbol"] for u in top if u["symbol"]])

    results = []
    for u in top:
        sym, name = u["symbol"], u["name"]
        ysym = (sym + ".NS") if sym else None
        g_closes = [c[4] for c in groww_candles(groww, sym, hist_s, hist_e, DAILY_INTERVAL_MIN)] if (groww and sym) else []
        y_closes = yahoo_closes(ysym)
        g_lean, g_note = trend_lean(g_closes)
        y_lean, y_note = trend_lean(y_closes)
        n_lean, n_hits = news_lean(name, news)
        w_lean = 0 if u["win_move"] is None else (1 if u["win_move"] > 0 else -1)

        price_flag = None
        if not sym:
            price_flag = "symbol unmapped — add slug to SLUG_MAP for Groww/Yahoo data"
        elif g_closes and y_closes:
            diff = abs(g_closes[-1] - y_closes[-1]) / y_closes[-1] * 100
            if diff > 2:
                price_flag = f"Groww/Yahoo price differ {diff:.1f}%"

        leans = {"Groww": g_lean, "Yahoo": y_lean, "News": n_lean, "Window": w_lean}
        signal = make_signal(leans)
        results.append({
            "symbol": sym or u["slug"], "name": name,
            "turnover": u["turnover"] or 0, "win_move": u["win_move"],
            "groww": {"last": g_closes[-1] if g_closes else None, "note": g_note, "lean": g_lean},
            "yahoo": {"last": y_closes[-1] if y_closes else None, "note": y_note, "lean": y_lean},
            "news": {"hits": n_hits, "lean": n_lean},
            "window_lean": w_lean, "price_flag": price_flag, "signal": signal,
            "catalyst": anns.get(sym) if sym else None,
        })

    return {
        "updated": now.strftime("%Y-%m-%d %H:%M:%S"),
        "window": f"{WINDOW_START.strftime('%H:%M')}-{WINDOW_END.strftime('%H:%M')} IST",
        "source": UNIVERSE_SOURCE,
        "groww_connected": groww is not None,
        "groww_error": groww_err,
        "scrape_enabled": ENABLE_SCRAPE,
        "stocks": results,
    }
