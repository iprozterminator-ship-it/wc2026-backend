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
    try:
        bets = await db.get_bets()
    except Exception as e:
        print(f"⚠️ get_bets error: {e}")
        bets = []
    try:
        cfg  = await db.get_config()
    except Exception as e:
        print(f"⚠️ get_config error: {e}")
        cfg = {}
    starting = float(cfg.get("starting_bankroll", 500.0))

    settled = [b for b in bets if b["status"] in ("won", "lost")]
    open_bets = [b for b in bets if b["status"] == "pending"]

    pnl = sum(
        b["actual_stake"] * b["actual_odds"] - b["actual_stake"] if b["status"] == "won"
        else -b["actual_stake"]
        for b in settled
    )
    # Deduct open stakes — money is committed the moment a bet is placed
    open_stake_total = sum(b["actual_stake"] for b in open_bets)
    bankroll = starting + pnl - open_stake_total
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
# AUTO-SETTLEMENT ENGINE
# ──────────────────────────────────────────────────────────────

def determine_bet_result(bet: dict, match: dict) -> str | None:
    """
    Returns 'won', 'lost', or None (if undetermined).
    Handles: 1X2, Over/Under, BTTS, Double Chance, Asian Handicap -0.5
    """
    score_home = match.get("score_home")
    score_away = match.get("score_away")
    if score_home is None or score_away is None:
        return None

    home_name   = match.get("home", "").lower()
    away_name   = match.get("away", "").lower()
    selection   = bet.get("selection", "").lower().strip()
    market      = bet.get("market", "").lower().strip()
    total_goals = score_home + score_away

    # ── 1X2 / Match Result ──────────────────────────────────────
    if any(k in market for k in ("h2h", "1x2", "match result", "match winner", "win")):
        if score_home > score_away:   # home win
            won = selection in (home_name, "home", "1", "home win") or home_name in selection
        elif score_home == score_away:  # draw
            won = selection in ("draw", "x", "tie", "match draw")
        else:                           # away win
            won = selection in (away_name, "away", "2", "away win") or away_name in selection
        return "won" if won else "lost"

    # ── Over / Under ────────────────────────────────────────────
    if "over" in selection or "under" in selection:
        try:
            parts = selection.split()
            line = float(parts[-1])
            if "over" in selection:
                return "won" if total_goals > line else "lost"
            else:
                return "won" if total_goals < line else "lost"
        except (ValueError, IndexError):
            pass

    # ── BTTS (Both Teams To Score) ──────────────────────────────
    if "btts" in market or "both teams" in selection:
        btts = score_home > 0 and score_away > 0
        if "yes" in selection:
            return "won" if btts else "lost"
        if "no" in selection:
            return "won" if not btts else "lost"

    # ── Double Chance ────────────────────────────────────────────
    if "double chance" in market or ("/" in selection and len(selection) <= 5):
        sel = selection.replace(" ", "")
        if sel in ("1/x", "home/draw"):
            return "won" if score_home >= score_away else "lost"
        if sel in ("x/2", "draw/away"):
            return "won" if score_home <= score_away else "lost"
        if sel in ("1/2", "home/away"):
            return "won" if score_home != score_away else "lost"

    # ── Asian Handicap -0.5 ──────────────────────────────────────
    if "-0.5" in selection or "ah" in market or "handicap" in market:
        if home_name in selection or "home" in selection:
            return "won" if score_home > score_away else "lost"
        if away_name in selection or "away" in selection:
            return "won" if score_away > score_home else "lost"

    # ── Team to win / qualify (outright-style on a single match) ─
    if home_name in selection:
       