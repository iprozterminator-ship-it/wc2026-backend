"""
WC2026 Trading Terminal — Autonomous Backend
FastAPI server that powers the live dashboard.
Connects to: football-data.org, The Odds API, NewsAPI, Claude API
"""

import os, json, asyncio
from datetime import datetime, timezone
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from database import db
from data_fetcher import fetcher
from agents import betting_agent, audit_agent
from scheduler import start_scheduler, scheduler

# ──────────────────────────────────────────────────────────────
# APP LIFECYCLE
# ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    print("WC2026 Agent starting...")
    await db.init()
    asyncio.create_task(_startup_cache())
    start_scheduler()
    yield
    scheduler.shutdown()
    print("WC2026 Agent stopped.")

async def _startup_cache():
    try:
        matches = await fetcher.get_wc_matches()
        odds    = await fetcher.get_live_odds()
        news    = await fetcher.get_news()
        if matches: await db.set_cache("matches", matches)
        if odds:    await db.set_cache("odds", odds)
        if news:    await db.set_cache("news", news)
        print(f"Startup cache: {len(matches)} matches, {len(odds)} odds, {len(news)} news")
    except Exception as e:
        print(f"Startup cache failed: {e}")

app = FastAPI(title="WC2026 Trading Terminal API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

active_connections: list[WebSocket] = []

async def broadcast(data: dict):
    msg = json.dumps(data)
    for ws in active_connections[:]:
        try:
            await ws.send_text(msg)
        except Exception:
            active_connections.remove(ws)

# ──────────────────────────────────────────────────────────────
# PYDANTIC MODELS
# ──────────────────────────────────────────────────────────────

class BetCreate(BaseModel):
    match: str
    date: str
    market: str
    selection: str
    rec_odds: float
    actual_odds: float
    rec_stake: float
    actual_stake: float
    confidence: float = 7.0
    risk: str = "Moderate"
    reasoning: str = ""
    is_accumulator: bool = False
    rec_id: Optional[str] = None

class BetSettle(BaseModel):
    status: str
    closing_odds: Optional[float] = None

class BetUpdate(BaseModel):
    status: Optional[str] = None
    closing_odds: Optional[float] = None
    actual_stake: Optional[float] = None
    actual_odds: Optional[float] = None

class BankrollUpdate(BaseModel):
    starting_bankroll: float

# ──────────────────────────────────────────────────────────────
# HEALTH
# ──────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "online", "time": datetime.now(timezone.utc).isoformat(), "agent": "WC2026"}

# ──────────────────────────────────────────────────────────────
# PORTFOLIO
# ──────────────────────────────────────────────────────────────

@app.get("/api/portfolio")
async def get_portfolio():
    try:
        bets = await db.get_bets()
    except Exception as e:
        print(f"get_bets error: {e}")
        bets = []
    try:
        cfg = await db.get_config()
    except Exception as e:
        print(f"get_config error: {e}")
        cfg = {}
    starting = float(cfg.get("starting_bankroll", 500.0))

    settled   = [b for b in bets if b["status"] in ("won", "lost")]
    open_bets = [b for b in bets if b["status"] == "pending"]

    pnl = sum(
        b["actual_stake"] * b["actual_odds"] - b["actual_stake"] if b["status"] == "won"
        else -b["actual_stake"]
        for b in settled
    )
    open_stake_total = sum(b["actual_stake"] for b in open_bets)
    bankroll  = starting + pnl - open_stake_total
    roi       = (pnl / starting * 100) if starting else 0
    won       = [b for b in settled if b["status"] == "won"]
    win_rate  = len(won) / len(settled) * 100 if settled else None

    clv_bets = [b for b in bets if b.get("closing_odds") and b.get("actual_odds")]
    avg_clv  = (
        sum((b["actual_odds"] / b["closing_odds"] - 1) * 100 for b in clv_bets) / len(clv_bets)
        if clv_bets else None
    )

    streak = 0
    for b in reversed(settled):
        if b["status"] == "won":
            streak += 1
        else:
            break

    return {
        "bankroll":         round(bankroll, 2),
        "starting_bankroll": starting,
        "pnl":              round(pnl, 2),
        "roi":              round(roi, 2),
        "win_rate":         round(win_rate, 1) if win_rate is not None else None,
        "open_count":       len(open_bets),
        "open_stake":       sum(b["actual_stake"] for b in open_bets),
        "settled_count":    len(settled),
        "won_count":        len(won),
        "avg_clv":          round(avg_clv, 2) if avg_clv is not None else None,
        "streak":           streak,
        "total_bets":       len(bets),
    }

@app.put("/api/portfolio/bankroll")
async def set_bankroll(data: BankrollUpdate):
    await db.set_config("starting_bankroll", data.starting_bankroll)
    return {"ok": True}

# ──────────────────────────────────────────────────────────────
# BETS
# ──────────────────────────────────────────────────────────────

@app.get("/api/bets")
async def get_bets(status: Optional[str] = None):
    bets = await db.get_bets()
    if status:
        bets = [b for b in bets if b["status"] == status]
    return bets

@app.post("/api/bets", status_code=201)
async def create_bet(bet: BetCreate):
    row = await db.create_bet(bet.dict())
    await broadcast({"type": "bet_created", "bet": row})
    return row

@app.put("/api/bets/{bet_id}")
async def update_bet(bet_id: str, data: BetUpdate):
    row = await db.update_bet(bet_id, data.dict(exclude_none=True))
    if not row:
        raise HTTPException(404, "Bet not found")
    await broadcast({"type": "bet_updated", "bet": row})
    return row

@app.post("/api/bets/{bet_id}/settle")
async def settle_bet(bet_id: str, data: BetSettle):
    updates = {"status": data.status}
    if data.closing_odds:
        updates["closing_odds"] = data.closing_odds
    row = await db.update_bet(bet_id, updates)
    if not row:
        raise HTTPException(404, "Bet not found")
    asyncio.create_task(generate_learning_entry(row))
    await broadcast({"type": "bet_settled", "bet": row})
    return row

@app.delete("/api/bets/{bet_id}")
async def delete_bet(bet_id: str):
    await db.delete_bet(bet_id)
    return {"ok": True}

@app.post("/api/reset")
async def reset_dashboard(starting_bankroll: float = 500.0):
    await db.reset_bets(starting_bankroll)
    await broadcast({"type": "reset", "bankroll": starting_bankroll})
    print(f"Dashboard reset. Bankroll: {starting_bankroll}")
    return {"ok": True, "message": f"Reset complete. Bankroll set to EUR{starting_bankroll:.2f}"}

# ──────────────────────────────────────────────────────────────
# RECOMMENDATIONS
# ──────────────────────────────────────────────────────────────

@app.get("/api/recommendations")
async def get_recommendations(status: Optional[str] = None):
    recs = await db.get_recommendations()
    if status:
        recs = [r for r in recs if r.get("audit_status") == status]
    return recs

@app.post("/api/run-agent")
async def run_agent_manually(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_analysis_cycle)
    return {"message": "Agent analysis cycle started. Check /api/recommendations in ~30s."}

# ──────────────────────────────────────────────────────────────
# LIVE DATA
# ──────────────────────────────────────────────────────────────

@app.get("/api/matches")
async def get_matches():
    return await db.get_cached("matches") or []

@app.get("/api/odds")
async def get_odds():
    return await db.get_cached("odds") or []

@app.get("/api/news")
async def get_news():
    return await db.get_cached("news") or []

@app.get("/api/alerts")
async def get_alerts():
    return await db.get_cached("alerts") or []

# ──────────────────────────────────────────────────────────────
# WHATSAPP BRIEF
# ──────────────────────────────────────────────────────────────

@app.get("/api/whatsapp-brief")
async def get_whatsapp_brief():
    port = await get_portfolio()
    recs = await get_recommendations()
    name = (await db.get_config()).get("agent_name", "Atlas")
    approved = [r for r in recs if r.get("audit_status") == "Approved"]
    today = datetime.now().strftime("%d %b %Y")
    sgn = "+" if port["pnl"] >= 0 else ""
    lines = [
        f"*WC2026 Intel -- {today}*",
        "",
        f"Bankroll: EUR{port['bankroll']:.2f} | ROI: {sgn}{port['roi']:.1f}%",
        f"P&L: {sgn}EUR{abs(port['pnl']):.2f} | Win Rate: {port['win_rate'] or '--'}%",
        "",
        "*TODAY'S APPROVED BETS*",
    ]
    for i, r in enumerate(approved[:4], 1):
        lines.append(f"{i}. {r['match']} -- {r['selection']}")
        lines.append(f"   Odds: {r.get('odds','?')} | Stake: EUR{r.get('recommended_stake','?')} | EV: +{r.get('ev',0):.1f}%")
        lines.append("")
    lines += [f"-- {name}"]
    return {"text": "\n".join(lines), "agent": name, "date": today}

# ──────────────────────────────────────────────────────────────
# WEBSOCKET
# ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        port = await get_portfolio()
        await websocket.send_text(json.dumps({"type": "init", "portfolio": port}))
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        if websocket in active_connections:
            active_connections.remove(websocket)

# ──────────────────────────────────────────────────────────────
# INTERNAL ENDPOINTS
# ──────────────────────────────────────────────────────────────

@app.get("/api/internal/refresh-live")
async def internal_refresh_live():
    await refresh_live()
    return {"ok": True}

@app.get("/api/internal/refresh-odds")
async def internal_refresh_odds():
    await refresh_odds()
    return {"ok": True}

@app.get("/api/internal/refresh-news")
async def internal_refresh_news():
    await refresh_news()
    return {"ok": True}

@app.post("/api/internal/auto-settle")
async def internal_auto_settle():
    await auto_settle_bets()
    return {"ok": True}

# ──────────────────────────────────────────────────────────────
# BACKGROUND FUNCTIONS (used by scheduler + endpoints)
# ──────────────────────────────────────────────────────────────

async def refresh_live():
    try:
        matches = await fetcher.get_wc_matches()
        if matches:
            await db.set_cache("matches", matches)
            await broadcast({"type": "matches_updated", "matches": matches})
    except Exception as e:
        print(f"refresh_live error: {e}")


async def refresh_odds():
    try:
        odds = await fetcher.get_live_odds()
        if odds:
            await db.set_cache("odds", odds)
            await broadcast({"type": "odds_updated", "odds": odds})
    except Exception as e:
        print(f"refresh_odds error: {e}")


async def refresh_news():
    try:
        news = await fetcher.get_news()
        if news:
            await db.set_cache("news", news)
            await broadcast({"type": "news_updated", "news": news})
    except Exception as e:
        print(f"refresh_news error: {e}")


async def run_analysis_cycle():
    print("Starting analysis cycle...")
    try:
        matches    = await db.get_cached("matches") or []
        odds       = await db.get_cached("odds") or []
        news       = await db.get_cached("news") or []
        candidates = await betting_agent.analyze(matches, odds, news)
        approved   = []
        for c in candidates:
            result = await audit_agent.review(c, matches, odds)
            if result.get("decision") in ("Approved", "Approved with Warnings"):
                c["audit_score"]  = result.get("score")
                c["audit_status"] = result.get("decision")
                c["audit_notes"]  = result.get("notes")
                approved.append(c)
                await db.upsert_recommendation(c)
        await broadcast({"type": "recommendations_updated", "count": len(approved)})
        print(f"Analysis cycle complete: {len(approved)} approved.")
    except Exception as e:
        print(f"run_analysis_cycle error: {e}")


def determine_bet_result(bet: dict, match: dict):
    score_home  = match.get("score_home")
    score_away  = match.get("score_away")
    if score_home is None or score_away is None:
        return None
    home_name   = match.get("home", "").lower()
    away_name   = match.get("away", "").lower()
    selection   = bet.get("selection", "").lower().strip()
    market      = bet.get("market", "").lower().strip()
    total_goals = score_home + score_away

    if any(k in market for k in ("h2h", "1x2", "match result", "match winner", "win")):
        if score_home > score_away:
            won = selection in (home_name, "home", "1", "home win") or home_name in selection
        elif score_home == score_away:
            won = selection in ("draw", "x", "tie", "match draw")
        else:
            won = selection in (away_name, "away", "2", "away win") or away_name in selection
        return "won" if won else "lost"

    if "over" in selection or "under" in selection:
        try:
            line = float(selection.split()[-1])
            return "won" if ("over" in selection and total_goals > line) or ("under" in selection and total_goals < line) else "lost"
        except (ValueError, IndexError):
            pass

    if "btts" in market or "both teams" in selection:
        btts = score_home > 0 and score_away > 0
        if "yes" in selection: return "won" if btts else "lost"
        if "no" in selection:  return "won" if not btts else "lost"

    if "double chance" in market or ("/" in selection and len(selection) <= 5):
        sel = selection.replace(" ", "")
        if sel in ("1/x", "home/draw"):  return "won" if score_home >= score_away else "lost"
        if sel in ("x/2", "draw/away"):  return "won" if score_home <= score_away else "lost"
        if sel in ("1/2", "home/away"):  return "won" if score_home != score_away else "lost"

    if "-0.5" in selection or "ah" in market or "handicap" in market:
        if home_name in selection or "home" in selection: return "won" if score_home > score_away else "lost"
        if away_name in selection or "away" in selection: return "won" if score_away > score_home else "lost"

    if home_name in selection: return "won" if score_home > score_away else "lost"
    if away_name in selection: return "won" if score_away > score_home else "lost"
    return None


async def auto_settle_bets():
    try:
        all_bets = await db.get_bets()
        pending  = [b for b in all_bets if b["status"] == "pending"]
        if not pending:
            return
        matches  = await fetcher.get_wc_matches()
        finished = [m for m in matches if m.get("status") == "FINISHED"]
        if not finished:
            return
        settled_count = 0
        for bet in pending:
            bet_match = bet.get("match", "").lower()
            for match in finished:
                home = match.get("home", "").lower()
                away = match.get("away", "").lower()
                if home in bet_match or away in bet_match:
                    result = determine_bet_result(bet, match)
                    if result:
                        updates = {"status": result}
                        await db.update_bet(bet["id"], updates)
                        asyncio.create_task(generate_learning_entry({**bet, **updates}))
                        await broadcast({"type": "bet_settled", "bet": {**bet, **updates}})
                        settled_count += 1
                        break
        if settled_count:
            port = await get_portfolio()
            await broadcast({"type": "portfolio_updated", "portfolio": port})
            print(f"Auto-settled {settled_count} bet(s).")
    except Exception as e:
        print(f"auto_settle_bets error: {e}")


async def generate_learning_entry(bet: dict):
    try:
        entry = await betting_agent.generate_learning_log(bet)
        if entry:
            await db.save_learning_log(entry)
    except Exception as e:
        print(f"generate_learning_entry error: {e}")
