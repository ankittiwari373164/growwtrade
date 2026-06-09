# Window Signals — 9:15–9:25 cross-source dashboard

Ranks NSE's most-active stocks (Groww's public "top by volume" page) and, for the
top names, cross-checks each across the Groww API, Yahoo, financial-news RSS, and
BSE corporate announcements. Shows a BUY/SELL/NEUTRAL signal from how many sources
agree, with a confidence level — plus a feedback **scorecard** that grades past
signals against actual (market-relative) outcomes.

> Signals are an indicator read, **not a prediction of price, and not financial
> advice**. When sources align it's a reason to look harder — not a green light.
> Opening-window data is noisy. Backtest with the scorecard before risking money,
> and size every position for being wrong.

No headless browser anywhere — pages are fetched with plain HTTP + `selectolax`,
so this runs on Vercel, Render, Oracle, or Docker.

## Sources
- **Groww API** — real intraday volume (window turnover) + daily candles.
- **Scrape (browser-free)** — top-volume / top-gainers page -> universe.
- **Yahoo** — independent price + indicators; flags >2% mismatch vs Groww.
- **RSS** — financial-news headlines, naive sentiment.
- **BSE** — latest corporate announcement as a "catalyst" context flag.

## Quick start (local)
```bash
pip install -r requirements.txt
cp .env.example .env          # optional; or paste the token in the UI
uvicorn app:app --reload --port 8000
# open http://localhost:8000  (scorecard at /scorecard)
```

## Deploy via GitHub
Push this folder to a repo, then:
- **Vercel** — import repo (uses vercel.json). Set DASHBOARD_SECRET. NOTE: the
  signal log does NOT persist on Vercel (ephemeral disk) — fine for live signals,
  but use a disk-backed host for the scorecard history.
- **Render** — New > Blueprint (uses render.yaml). Set DASHBOARD_SECRET. Add a
  persistent disk mounted at DATA_DIR if you want the scorecard to accumulate.
- **Oracle / any VM (Docker)** — best for durable logging:
  ```bash
  docker build -t window-signals .
  docker run -d --restart unless-stopped -p 80:8000 \
    -e DASHBOARD_SECRET="your-secret" -e DATA_DIR=/data \
    -v /opt/winsig-data:/data window-signals
  ```
  Open the port in the Oracle VCN security list AND the instance iptables, and
  keep it behind auth/HTTPS (the token can place orders).

## Daily routine
1. **Morning:** generate the Groww access token locally (keep the TOTP secret off
   the server), paste it + your dashboard secret into the top bar, click Set token:
   ```python
   from growwapi import GrowwAPI; import pyotp
   print(GrowwAPI.get_access_token(api_key="...", totp=pyotp.TOTP("SECRET").now()))
   ```
   The morning signals auto-log once, after the 9:25 window.
2. **Evening (~15:45 IST):** trigger outcome grading once:
   ```
   curl -X POST https://your-app/api/log-outcomes -H "X-Secret: your-secret"
   ```
   Automate with Render Cron, a free uptime pinger, or a VM crontab.
3. Review `/scorecard`.

## Endpoints
- `GET /` dashboard · `GET /scorecard` feedback view
- `GET /api/data` analyzed stocks (cached; `?force=1`)
- `POST /api/token` body `{"token":"..."}`, header `X-Secret`
- `POST /api/log-outcomes` header `X-Secret` (grades the day)
- `GET /api/scorecard` aggregated hit-rates · `GET /api/status`

## Env vars
DASHBOARD_SECRET (required), ENABLE_SCRAPE, UNIVERSE_SOURCE (top-volume|top-gainers
|top-losers), TOP_N, CACHE_TTL, DATA_DIR, GROWW_ACCESS_TOKEN (optional). See
.env.example.

## Notes / tweaks
- Window: WINDOW_START / WINDOW_END in analysis.py.
- Unmapped movers appear but show "—" for Groww/Yahoo until you add the slug to
  SLUG_MAP (analysis.py). Verify the NSE ticker before adding — a wrong ticker
  pulls the wrong stock's data.
- BSE scrip codes: SCRIP_MAP in announcements.py.
- Confirm Groww's get_access_token TOTP signature and interval_in_minutes against
  current Groww API docs.
- Read the scorecard skeptically: the only question that matters is whether
  "medium" confidence beats "low" and beats 50% net of market, over MONTHS.
