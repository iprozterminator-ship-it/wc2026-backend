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
    """Start background scheduler on boot, stop on shutdown."""
    print("🚀 WC2026 Agent starting...")
    await db.init()
    start_scheduler()
    yield
    scheduler.shutdown()
    print("🛑 WC2026 Agent stopped.")

app = FastAPI(title="WC2026 Trading Terminal API", version="1.0.0", lifespan=lifespan)

# Allow your frontend (any origin for now — restrict in production)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Active WebSocket connections (for live push)
active_connections: list[WebSocket] = []

async def broadcast(data: dict):
    """Push data to all connected dashboard clients."""
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
    status: str          # "won" | "lost" | "void"
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
    bets = await db.get_bets()
    cfg  = await db.get_config()
    starting = cfg.get("starting_bankroll", 500.0)

    settled = [b for b in bets if b["status"] in ("won", "lost")]
    open_bets = [b for b in bets if b["status"] == "pending"]

    pnl = sum(
        b["actual_stake"] * b["actual_odds"] - b["actual_stake"] if b["status"] == "won"
        else -b["actual_stake"]
        for b in settled
    )
    bankroll = starting + pnl
    roi = (pnl / starting * 100) if starting else 0
    won = [b for b in settled if b["status"] == "won"]
    win_rate = len(won) / len(settled) * 100 if settled else None

    clv_bets = [b for b in bets if b.get("closing_odds") and b.get("actual_odds")]
    avg_clv = (
        sum((b["actual_odds"] / b["closing_odds"] - 1) * 100 for b in clv_bets) / len(clv_bets)
        if clv_bets else None
    )

    # Streak
    streak = 0
    for b in reversed(settled):
        if b["status"] == "won":
            streak += 1
        else:
            break

    return {
        "bankroll": round(bankroll, 2),
        "starting_bankroll": starting,
        "pnl": round(pnl, 2),
        "roi": round(roi, 2),
        "win_rate": round(win_rate, 1) if win_rate is not None else None,
        "open_count": len(open_bets),
        "open_stake": sum(b["actual_stake"] for b in open_bets),
        "settled_count": len(settled),
        "won_count": len(won),
        "avg_clv": round(avg_clv, 2) if avg_clv is not None else None,
        "streak": streak,
        "total_bets": len(bets),
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
    row = await db.create_bet(bet.model_dump())
    await broadcast({"type": "bet_created", "bet": row})
    return row

@app.put("/api/bets/{bet_id}")
async def update_bet(bet_id: str, data: BetUpdate):
    row = await db.update_bet(bet_id, data.model_dump(exclude_none=True))
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

    # Auto-generate learning log entry
    asyncio.create_task(generate_learning_entry(row))
    await broadcast({"type": "bet_settled", "bet": row})
    return row

@app.delete("/api/bets/{bet_id}")
async def delete_bet(bet_id: str):
    await db.delete_bet(bet_id)
    return {"ok": True}

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
    """Manually trigger the full analysis cycle."""
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
    """Generate today's WhatsApp message."""
    port = await get_portfolio()
    recs = await get_recommendations()
    name = (await db.get_config()).get("agent_name", "Atlas")
    approved = [r for r in recs if r.get("audit_status") == "Approved"]

    today = datetime.now().strftime("%d %b %Y")
    sgn = "+" if port["pnl"] >= 0 else ""
    lines = [
        f"*WC2026 Intel — {today}*",
        "",
        f"Bankroll: €{port['bankroll']:.2f} | ROI: {sgn}{port['roi']:.1f}%",
        f"P&L: {sgn}€{abs(port['pnl']):.2f} | Win Rate: {port['win_rate'] or '—'}%",
        "",
        "*TODAY'S APPROVED BETS*",
    ]
    for i, r in enumerate(approved[:4], 1):
        lines.append(f"{['1️⃣','2️⃣','3️⃣','4️⃣'][i-1]} {r['match']} — {r['selection']}")
        lines.append(f"   Odds: {r['odds']} | Stake: €{r['recommended_stake']} | EV: +{r['ev']:.1f}%")
        lines.append("")
    lines += [f"— {name}"]

    return {"text": "\n".join(lines), "agent": name, "date": today}

# ──────────────────────────────────────────────────────────────
# WEBSOCKET — LIVE PUSH
# ──────────────────────────────────────────────────────────────

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    active_connections.append(websocket)
    try:
        # Send initial state on connect
        port = await get_portfolio()
        await websocket.send_text(json.dumps({"type": "init", "portfolio": port}))
        # Keep alive
        while True:
            await asyncio.sleep(30)
            await websocket.send_text(json.dumps({"type": "ping"}))
    except WebSocketDisconnect:
        active_connections.remove(websocket)

# ──────────────────────────────────────────────────────────────
# AGENT ANALYSIS CYCLE (called by scheduler)
# ──────────────────────────────────────────────────────────────

async def run_analysis_cycle():
    """
    Morning intelligence cycle:
    1. Fetch latest match + odds + news data
    2. Run Betting Agent (Claude) → generates recommendations
    3. Run Audit Agent (Claude) → approves / rejects each
    4. Store approved recs in DB
    5. Push to connected clients
    """
    print(f"🤖 Running analysis cycle at {datetime.now().isoformat()}")
    try:
        # 1. Fetch data
        matches = await fetcher.get_wc_matches()
        odds    = await fetcher.get_live_odds()
        news    = await fetcher.get_news()

        await db.set_cache("matches", matches)
        await db.set_cache("odds", odds)
        await db.set_cache("news", news)

        # 2. Betting Agent generates candidates
        candidates = await betting_agent.analyze(matches, odds, news)
        print(f"  Betting Agent: {len(candidates)} candidates generated")

        # 3. Audit Agent reviews each
        approved = []
        for rec in candidates:
            audit = await audit_agent.review(rec)
            rec["audit_score"]  = audit["score"]
            rec["audit_status"] = audit["status"]
            rec["audit_notes"]  = audit["notes"]
            rec["audit_warnings"] = audit.get("warnings", [])
            if audit["status"] != "Rejected":
                approved.append(rec)

        print(f"  Audit Agent: {len(approved)}/{len(candidates)} approved")

        # 4. Save to DB
        for rec in approved:
            await db.upsert_recommendation(rec)

        # 5. Push to clients
        await broadcast({"type": "recommendations_updated", "count": len(approved)})
        print(f"✅ Analysis cycle complete. {len(approved)} recommendations live.")

    except Exception as e:
        print(f"❌ Analysis cycle failed: {e}")
        await broadcast({"type": "error", "message": str(e)})

async def generate_learning_entry(settled_bet: dict):
    """After a bet settles, ask Claude to reflect on the prediction."""
    try:
        won = settled_bet["status"] == "won"
        entry = await betting_agent.reflect(settled_bet, won)
        await db.save_learning_log(entry)
        await broadcast({"type": "learning_log", "entry": entry})
    except Exception as e:
        print(f"Learning log error: {e}")

# ──────────────────────────────────────────────────────────────
# REFRESH ENDPOINTS (called by scheduler)
# ──────────────────────────────────────────────────────────────

@app.post("/api/internal/refresh-live")
async def refresh_live():
    """Called every 15s to refresh live match data."""
    matches = await fetcher.get_live_matches()
    await db.set_cache("matches", matches)
    await broadcast({"type": "matches_updated", "matches": matches})
    return {"ok": True}

@app.post("/api/internal/refresh-odds")
async def refresh_odds():
    """Called every 60s to refresh odds."""
    odds = await fetcher.get_live_odds()
    await db.set_cache("odds", odds)
    await broadcast({"type": "odds_updated", "odds": odds})
    return {"ok": True}

@app.post("/api/internal/refresh-news")
async def refresh_news():
    """Called every 5min to refresh news."""
    news = await fetcher.get_news()
    await db.set_cache("news", news)
    await broadcast({"type": "news_updated", "news": news})
    return {"ok": True}
