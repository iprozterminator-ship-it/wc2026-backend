# WC2026 Trading Terminal — Claude Code Briefing

## What this project is

A professional sports trading and portfolio management platform for betting on the 2026 FIFA World Cup. It combines a live trading terminal dashboard with an autonomous Python backend that fetches football data, generates betting recommendations via Claude AI, and manages a €500 portfolio.

The tone is institutional — Bloomberg Terminal meets sports betting. Not a gambling app. A self-learning, risk-controlled trading desk.

---

## Current state

### ✅ Done
- **Frontend dashboard** (`wc2026.html`) — complete, ~1400 lines, opens in any browser
- **Python backend** (`backend/`) — complete, deployed to Railway
- **Supabase database** — live, all 8 tables created
- **Railway deployment** — successful, running Python 3.11

### ⏳ Still needed
- Connect dashboard to live Railway API (replace hardcoded data with API calls)
- Add Railway domain/URL to the HTML as `API_BASE`
- Expose Railway service + configure environment variables
- Test end-to-end flow
- Optionally: deploy dashboard to Vercel

---

## File structure

```
Sports betting/
├── wc2026.html              ← Complete frontend (open in browser)
├── CLAUDE.md                ← This file
├── SETUP_GUIDE.md           ← Setup instructions (do not commit to git)
└── backend/
    ├── main.py              ← FastAPI app — all API endpoints
    ├── agents.py            ← Betting Agent + Audit Agent (Claude API)
    ├── data_fetcher.py      ← Connects to football-data.org, Odds API, NewsAPI
    ├── database.py          ← Supabase client + local JSON fallback
    ├── scheduler.py         ← APScheduler — runs jobs every 15s/60s/daily
    ├── requirements.txt     ← Python deps (pydantic v1 to avoid Rust build issue)
    ├── runtime.txt          ← python-3.11
    └── .env.example         ← Template for env vars (never commit .env)
```

---

## Tech stack

| Layer | Technology |
|---|---|
| Frontend | Vanilla HTML/CSS/JS, Chart.js, JetBrains Mono font |
| Backend | Python 3.11, FastAPI 0.95, Uvicorn |
| Database | Supabase (PostgreSQL) with local JSON fallback |
| AI Agents | Anthropic Claude API (claude-opus-4-6) |
| Scheduler | APScheduler (cron + interval jobs) |
| Data APIs | football-data.org, The Odds API, NewsAPI |
| Hosting | Railway (backend), Vercel (frontend), Supabase (db) |
| Realtime | WebSocket (`/ws` endpoint) for live dashboard push |

---

## Architecture

```
Browser (wc2026.html)
    ↕ REST + WebSocket
Railway (FastAPI backend)
    ↕                    ↕                  ↕
football-data.org    The Odds API       Claude API
(live scores)        (Betclic odds)     (AI agents)
    ↕
Supabase (PostgreSQL)
(bets, recommendations, learning log, config)
```

**Scheduler jobs:**
- Every 15s → refresh live match scores
- Every 60s → refresh Betclic odds
- Every 5min → refresh news feed
- 08:00 UTC daily → full AI analysis cycle (Betting Agent → Audit Agent → publish recs)
- 16:00 UTC daily → second analysis cycle
- On bet settle → Claude generates learning log entry

---

## API endpoints (main.py)

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness check |
| GET | `/api/portfolio` | Bankroll, P&L, ROI, win rate |
| GET | `/api/bets` | All trades (filter by `?status=pending`) |
| POST | `/api/bets` | Create a new bet |
| PUT | `/api/bets/{id}` | Update a bet |
| POST | `/api/bets/{id}/settle` | Settle won/lost + trigger learning log |
| DELETE | `/api/bets/{id}` | Delete a bet |
| GET | `/api/recommendations` | AI-generated approved bets |
| POST | `/api/run-agent` | Manually trigger analysis cycle |
| GET | `/api/matches` | Live/upcoming WC matches |
| GET | `/api/odds` | Current Betclic odds |
| GET | `/api/news` | Football news feed |
| GET | `/api/whatsapp-brief` | Generate daily WhatsApp message |
| PUT | `/api/portfolio/bankroll` | Update starting bankroll |
| WS | `/ws` | WebSocket for live push to dashboard |

---

## Dual-agent system (agents.py)

**Betting Agent** — calls Claude API with match + odds + news data. Returns 2–5 trade candidates with EV, confidence, rationale, risk.

**Audit Agent** — independently reviews each candidate as Chief Risk Officer. Assigns a score (1–10) and approves / approves with warnings / rejects. Only approved recs appear in the dashboard.

**Learning loop** — after every bet settles, Claude writes a self-reflection entry comparing prediction vs reality and suggesting model adjustments.

---

## Database schema (Supabase)

Tables already created:
- `bets` — trade ledger (rec_odds + actual_odds, rec_stake + actual_stake)
- `recommendations` — AI-generated, audit-scored trade ideas
- `learning_log` — post-match AI self-reflection
- `config` — bankroll, agent name, settings
- `accumulators` — combined bets
- `match_cache` — live score data
- `odds_cache` — live Betclic odds
- `news_cache` — news feed

---

## Environment variables (set in Railway → Variables)

```
ANTHROPIC_API_KEY      Claude API key
FOOTBALL_DATA_KEY      football-data.org key
ODDS_API_KEY           the-odds-api.com key
NEWS_API_KEY           newsapi.org key
SUPABASE_URL           https://your-project.supabase.co
SUPABASE_KEY           Supabase anon/public key
PORT                   Set automatically by Railway
```

**Never store keys in code or markdown files. Railway Variables only.**

---

## Frontend dashboard (wc2026.html)

**Tabs (keyboard shortcuts 1–8):**
1. Dashboard — bankroll, equity curve, milestones, action center, opportunities
2. Markets — singles feed + accumulator engine + daily betting card
3. Portfolio — exposure breakdown, Kelly sizing, open positions
4. Trades — full ledger with CLV tracking, rec vs actual odds
5. Live — live match center
6. WhatsApp — agent identity (naming system), daily briefs, newsletter
7. News — news terminal with category filters
8. Risk — hard controls, CLV tracker, audit workflow

**Key features:**
- Agent naming system (Atlas / Scout / Kai / Finn / Nova) — locked for the tournament
- One-click "Place Bet" execution modal (records recommended vs actual stake/odds)
- Accumulator builder (3 pre-built: Conservative 2.55x, Balanced 2.77x, High Upside 6.12x)
- WhatsApp daily brief generator with Copy button
- Milestone tracker (€500 → €1K → €5K → €10K → €25K)
- All data currently hardcoded — needs to be replaced with API calls to Railway

---

## Immediate next steps

1. **Get the Railway URL** — Settings → Networking → Generate Domain
2. **Add API base to dashboard** — find `let STATE = {` in wc2026.html and add `const API_BASE = "https://your-railway-url.up.railway.app";` after it
3. **Replace hardcoded data with API calls** — the key functions to update:
   - `renderFeeds()` → fetch from `/api/recommendations`
   - `renderPositions()` → fetch from `/api/bets?status=pending`
   - `renderLedger()` → fetch from `/api/bets`
   - `updateHeader()` → fetch from `/api/portfolio`
   - `renderNews()` → fetch from `/api/news`
   - `renderFixtures()` → fetch from `/api/matches`
4. **Connect WebSocket** — add `const ws = new WebSocket(API_BASE.replace('https','wss') + '/ws');` for live updates
5. **Test end-to-end** — place a test bet, check it appears in Supabase

---

## Important context

- **French market only** — recommendations filtered for Betclic France (ANJ regulated)
- **No sportsbook integration** — user places bets manually on Betclic, then confirms in the app
- **Bankroll: €500 starting**
- **Pydantic v1** — requirements.txt pins pydantic==1.10.21 (v2 caused Rust build failure on Railway)
- **SETUP_GUIDE.md** — contains notes; do not commit to public GitHub
- **World Cup 2026** — June 11 to July 19, 2026. Tournament hasn't started yet as of June 2026.
- **Group I (France's group)**: France, Norway, Senegal, Iraq. Strongest group by ELO average (1870).

---

## Pre-loaded recommendations (hardcoded in HTML, will come from API later)

| Match | Selection | Odds | EV | Audit |
|---|---|---|---|---|
| France vs Iraq | France Win | 1.20 | +7.4% | 9.2/10 ✅ |
| France vs Iraq | Over 3.5 Goals | 1.75 | +12.0% | 8.1/10 ⚠ |
| Brazil vs Morocco | Brazil Win | 1.65 | +8.9% | 7.4/10 ⚠ |
| Senegal vs Norway | Over 2.5 Goals | 1.90 | +14.1% | 8.6/10 ✅ |
| France Group I Winner | 1st Place | 1.55 | +5.4% | 7.8/10 ✅ |
| Norway Qualification | To Qualify | 1.40 | +14.9% | 9.0/10 ✅ |
