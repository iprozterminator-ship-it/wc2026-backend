# WC2026 Trading Terminal — Session Log

## Project Summary
A professional sports trading dashboard for the 2026 FIFA World Cup. Bloomberg Terminal aesthetic. €500 starting bankroll. Institutional model, not a gambling app.

**File:** `wc2026.html` (~176KB, ~2600 lines) — single-file frontend  
**Frontend (Vercel):** `https://wc2026-nine-iota.vercel.app`  
**Backend (Railway):** `https://wc2026-backend-production-dd79.up.railway.app`  
**DB:** Supabase (PostgreSQL)  
**AI:** Claude API (claude-opus-4-6) via dual-agent system  

---

## What's Been Built

### Frontend (`wc2026.html`)
8-tab dashboard with keyboard shortcuts (1–8):
1. **Dashboard** — bankroll, equity curve, milestones, alerts, action center
2. **Markets** — browse all matches, tournament outrights, AI recs, accumulator engine, daily card
3. **Portfolio** — Kelly sizing, exposure breakdown, open positions
4. **Trades** — full ledger, CLV tracking, rec vs actual odds
5. **Live** — live match center
6. **WhatsApp** — agent identity, daily briefs, newsletter
7. **News** — news terminal with category filters
8. **Risk** — hard controls, CLV tracker, audit workflow, Model v4 methodology panel

### Key Features
- **Bet Basket** (right-side drawer) — Simple bets + Combiné (accumulator) tabs, sportsbook-style
- **Match Browser** — all 16 fixtures with full market types: 1X2, Double Chance, BTTS, O/U 1.5/2.5/3.5, AH −0.5
- **Tournament Outrights** — Group Winners, Qualify Top 2, Reach QF/SF, Win WC (40+ items)
- **Poisson model** — xG from ELO, market odds from Poisson distribution
- **Environmental factors** — altitude (Azteca 2240m, Empower 1609m) + heat penalties, silently adjusts model probs
- **Kickoff countdowns** — live countdown per fixture, LOCKED badge once match starts, betting panel replaced with lock message
- **WebSocket** — exponential backoff reconnect (1s→2s→4s…→30s cap), reconnects on tab focus
- **WS indicator** — red/green dot + OFFLINE/LIVE label in header
- **Run Agent button** — `⚡ Run Agent` triggers `POST /api/run-agent` on Railway
- **Optimistic bet placement** — balance deducts instantly, Trades tab opens, API sync in background
- **Agent naming system** — Atlas/Scout/Kai/Finn/Nova, locked for the tournament
- **Accumulator builder** — 3 pre-built with mixed-market validation
- **Milestone tracker** — €500→€1K→€5K→€10K→€25K
- **Balance animation** — up/down color flash on bankroll changes

### Model v4
- **ELO-based xG:** `xGH = 1.35 + eloDiff/650` (+60 home advantage)
- **Environmental adjustment:** altitude (Europeans −4.5% at 2000m+), heat (Europeans −0.3%/°C above 30°C)
- **Fractional Kelly (40%)** with uncertainty multiplier + drawdown multiplier
- **Peak bankroll tracking** for drawdown calculation
- **Daily exposure cap** — 20% of bankroll (hard check in placeBasket)
- **Mixed-market accumulator validation** — ≥1 non-O/U leg required
- **Dual-agent audit** — Betting Agent → Audit Agent (CRO) → approved recs only shown

### Backend (`backend/`)
- `main.py` — FastAPI, all endpoints
- `agents.py` — Betting Agent + Audit Agent + Learning Loop
- `data_fetcher.py` — football-data.org, Odds API, NewsAPI
- `database.py` — Supabase + local JSON fallback
- `scheduler.py` — APScheduler (15s scores, 60s odds, 5min news, 08:00/16:00 UTC agent cycles)

---

## Current State (as of Jun 7 2026)
- ✅ Frontend complete and functional
- ✅ Railway backend deployed and running
- ✅ Supabase DB live (8 tables)
- ✅ API connected (`API_BASE` set to Railway URL)
- ✅ WebSocket live push working
- ✅ Basket → placeBasket → optimistic update working
- ✅ Kickoff times + lock logic on all 16 fixtures
- ⏳ Tournament hasn't started yet (starts Jun 11)
- ⏳ Real-time odds not yet flowing from Odds API into FIXTURES.h/d/a
- ⏳ Kelly stakes in RECS are hardcoded (don't recalculate as bankroll changes)

---

## Key Data

### Group I (France's group — highest ELO avg 1870)
| Team | ELO |
|---|---|
| France | 1980 |
| Norway | 1700 |
| Senegal | 1665 |
| Iraq | 1490 |

### Key Fixtures
| Date | Match | Venue | Note |
|---|---|---|---|
| Jun 17 | France vs Iraq | MetLife, NY | France opener |
| Jun 17 | Senegal vs Norway | Arrowhead, KC | Haaland vs Teranga Lions |
| Jun 22 | France vs Senegal | Arrowhead, KC | |
| **Jun 26** | **France vs Norway** | **Azteca, MEX CITY** | ⚠️ 2240m altitude — model adjusted |

### Pre-loaded Recommendations
| Match | Selection | Odds | EV | Audit |
|---|---|---|---|---|
| France vs Iraq | France Win | 1.20 | +7.4% | 9.2/10 ✅ |
| France vs Iraq | Over 3.5 Goals | 1.75 | +12.0% | 8.1/10 ⚠ |
| Brazil vs Morocco | Brazil Win | 1.65 | +8.9% | 7.4/10 ⚠ |
| Senegal vs Norway | Over 2.5 Goals | 1.90 | +14.1% | 8.6/10 ✅ |
| France Group I Winner | 1st Place | 1.55 | +5.4% | 7.8/10 ✅ |
| Norway Qualification | To Qualify | 1.40 | +14.9% | 9.0/10 ✅ |

---

## Known Missing Pieces (backlog)

1. **Real-time odds sync** — FIXTURES.h/d/a still hardcoded; need Odds API to update them live
2. **Kelly recalculation** — RECS stakes are static; should recalculate as bankroll changes
3. **Group stage simulation** — no projected knockout bracket yet
4. **CLV auto-population** — closing odds not pulled automatically when match starts
5. **No bet amendment** — only delete + re-add; no edit flow
6. **Large-bet confirmation gate** — no "are you sure?" for bets >€50
7. **Tournament outright auto-settlement** — when a team is eliminated, void those bets
8. **Accumulator partial leg tracking** — combi bets don't track individual leg results
9. **No full device sync** — if opened on new device, local state is empty (Railway API is source of truth but re-sync on load not fully tested)
10. **WebSocket no auth** — /ws endpoint is open; fine for now, revisit before sharing

---

## Environment Variables (Railway)
```
ANTHROPIC_API_KEY
FOOTBALL_DATA_KEY
ODDS_API_KEY
NEWS_API_KEY
SUPABASE_URL
SUPABASE_KEY
PORT  (auto-set by Railway)
```
**Never store keys in code or markdown files.**

---

## Useful Links
- Railway dashboard → check logs, env vars, deploy status
- Supabase → inspect tables live
- football-data.org → match/score data
- the-odds-api.com → Betclic-adjacent odds (French market)

---

## Mobile Usage
Cowork is desktop-only. From phone, use Claude.ai (normal chat) for:
- Betting analysis / strategy questions
- Generating WhatsApp daily briefs manually
- Tournament research

Use the **📲 Daily Brief** button in the dashboard to generate a copy-paste WhatsApp summary.
