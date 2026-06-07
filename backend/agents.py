"""
WC2026 Dual-Agent System — Institutional Methods v3
─────────────────────────────────────────────────────
Betting Agent:  Multi-signal probability engine with institutional sizing
Audit Agent:    Chief Risk Officer — challenges every bet, can reject

Institutional methods integrated:
  • Fractional Kelly (40%)        — optimal fraction, uncertainty-adjusted
  • Model Uncertainty Weighting   — disagrement between model & market
  • Drawdown Circuit Breaker      — stake scaling based on current DD
  • Closing Line Value (CLV)      — quality signal, not just EV
  • Correlation-Penalised Accus   — Markowitz-style accumulator selection
  • Betting Sharpe Awareness      — target consistent edge, not outlier bets
"""

import os, json, math
from datetime import datetime
from anthropic import AsyncAnthropic

client = AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL  = "claude-opus-4-6"

# ──────────────────────────────────────────────────────────────
# INSTITUTIONAL SIZING HELPERS  (mirrors wc2022_backtest.py v3)
# ──────────────────────────────────────────────────────────────

def kelly_full(prob: float, odds: float) -> float:
    """Full Kelly criterion fraction (unbounded)."""
    b = odds - 1.0
    return max(0.0, (prob * b - (1 - prob)) / b)

def model_uncertainty(model_p: float, market_p: float, odds: float) -> float:
    """
    Uncertainty score 0.0–0.5.
    Large model/market disagreement → higher uncertainty → smaller position.
    Analogous to Bayesian posterior width in portfolio sizing.
    """
    disagreement = abs(model_p - market_p)
    odds_risk    = min((odds - 1) / 25.0, 0.15)
    return min(disagreement * 1.8 + odds_risk, 0.5)

def drawdown_multiplier(current_bankroll: float, peak_bankroll: float) -> float:
    """
    Institutional drawdown circuit breaker (Winton / AQR style).
    DD 0-5%: 1.0 | 5-10%: 0.75 | 10-20%: 0.50 | >20%: 0.25
    """
    if peak_bankroll <= 0:
        return 1.0
    dd = (peak_bankroll - current_bankroll) / peak_bankroll * 100
    if   dd < 5:  return 1.00
    elif dd < 10: return 0.75
    elif dd < 20: return 0.50
    else:         return 0.25

def institutional_stake(
    model_prob: float,
    odds: float,
    bankroll: float,
    market_prob: float,
    peak_bankroll: float,
    kelly_fraction: float = 0.40,
    max_stake: float = 25.0,
) -> dict:
    """
    Full institutional stake calculation.
    Returns dict with stake, full_kelly_pct, uncertainty, dd_multiplier.
    """
    fk     = kelly_full(model_prob, odds)
    uncert = model_uncertainty(model_prob, market_prob, odds)
    dd     = drawdown_multiplier(bankroll, peak_bankroll)

    adj_frac = fk * kelly_fraction * (1.0 - uncert * 0.8) * dd
    stake    = round(min(bankroll * adj_frac, max_stake))

    return {
        "stake":           max(stake, 0),
        "full_kelly_pct":  round(fk * 100, 1),
        "uncertainty":     round(uncert, 3),
        "dd_multiplier":   round(dd, 2),
        "kelly_fraction":  kelly_fraction,
    }


# ──────────────────────────────────────────────────────────────
# BETTING AGENT
# ──────────────────────────────────────────────────────────────

class BettingAgent:

    async def analyze(
        self,
        matches: list,
        odds: list,
        news: list,
        portfolio: dict | None = None,
    ) -> list:
        """
        Generate betting recommendations using institutional-grade signals.
        portfolio dict: { "bankroll": 500, "peak_bankroll": 500, "open_positions": [] }
        """
        bankroll      = (portfolio or {}).get("bankroll", 500)
        peak_bankroll = (portfolio or {}).get("peak_bankroll", bankroll)
        dd_mult       = drawdown_multiplier(bankroll, peak_bankroll)
        dd_note       = (
            "⚠️ DRAWDOWN ALERT: Circuit breaker active. Reduce all stakes by "
            f"{int((1-dd_mult)*100)}% from normal sizing."
            if dd_mult < 1.0 else "No active drawdown."
        )

        prompt = f"""
You are an elite football trading analyst for the 2026 FIFA World Cup.
You operate like a quant fund — not a gambler. Every recommendation must pass
institutional-grade criteria before being submitted for audit.

━━━ LIVE DATA ({datetime.now().strftime('%Y-%m-%d %H:%M UTC')}) ━━━

PORTFOLIO STATE:
  Current Bankroll: €{bankroll:.2f}
  Peak Bankroll:    €{peak_bankroll:.2f}
  Drawdown Mult:    {dd_mult:.2f}  ← {dd_note}

UPCOMING MATCHES:
{json.dumps(matches[:20], indent=2)}

CURRENT BETCLIC ODDS:
{json.dumps(odds[:30], indent=2)}

BREAKING NEWS & INTELLIGENCE:
{json.dumps(news[:10], indent=2)}

━━━ ANALYSIS METHODOLOGY ━━━

Step 1 — DYNAMIC PROBABILITY ESTIMATION
  For each match build a weighted probability blend:
  • 65% weight: ELO-based model probability
    - Adjust base ELO for tournament form (last 3 results: +35/W, -20/L)
    - Lineup quality factor: 1.0=full squad, 0.72=heavy rotation → × ±160 ELO pts
    - Rest days: ≥5 days +12 ELO, ≤2 days -22 ELO
    - Motivation: must-win × 1.06, already qualified rotation × 0.88
  • 30% weight: Market-implied probability (bookmaker odds stripped of margin)
    - Strip margin: P_market = (1/odds) / Σ(1/all_outcomes)
  • 5%  weight: H2H adjustment (known historical tendencies)

Step 2 — EXPECTED VALUE GATE (ALL must pass)
  ✓ EV ≥ 5%           (EV = model_prob × (odds-1) − (1−model_prob))
  ✓ Odds ≥ 1.40        (avoid unquantifiable short prices)
  ✓ Profit ≥ €5        (floor on minimum return)
  ✓ Confidence ≥ 7.0   (model conviction threshold)

Step 3 — INSTITUTIONAL STAKE SIZING
  Do NOT use fixed stake tiers. Use the Kelly formula:
  Full Kelly = (p×(odds-1) - (1-p)) / (odds-1)
  Final stake = Bankroll × Full Kelly × 0.40 × (1 - uncertainty×0.8) × {dd_mult:.2f}

  Uncertainty = |model_prob - market_prob| × 1.8 + (odds-1)/25
  Cap: never exceed €25 per single bet.

Step 4 — CLOSING LINE VALUE (CLV) CHECK
  Strong bets beat the closing line. Ask: "If I bet this now, would sharp money
  confirm this price as value by kick-off?" Bets where the market will likely
  move in our favour are high-CLV. Flag bets where market may move against us.

Step 5 — MARKET INTELLIGENCE
  • Flag any rotation/lineup intelligence from news
  • Note if market is soft (recreational) vs sharp (limit)
  • Consider correlation with any existing open positions:
    {json.dumps((portfolio or {}).get('open_positions', []))}

━━━ OUTPUT FORMAT ━━━

Return a JSON array. Each item must have exactly these fields:
{{
  "match": "Team A vs Team B",
  "date": "YYYY-MM-DD",
  "market": "1X2|O/U 2.5|DNB|BTTS|Handicap|Group Winner|To Qualify",
  "selection": "exact selection name",
  "odds": 1.75,
  "fair_odds": 1.60,
  "model_probability": 62.5,
  "market_implied_probability": 57.1,
  "edge": 5.4,
  "ev": 9.4,
  "confidence": 7.5,
  "uncertainty_score": 0.12,
  "clv_outlook": "Positive|Neutral|Negative",
  "risk": "Low|Moderate|High",
  "recommended_stake": 15,
  "kelly_pct": 8.5,
  "dd_adjustment": {dd_mult},
  "lineup_flag": "Full Squad|Rotation Risk|Confirmed Rotation",
  "tier": 1,
  "rationale": "3-4 sentences: model reasoning, edge source, news intelligence",
  "factors": ["factor 1", "factor 2", "factor 3"],
  "risks": ["risk 1", "risk 2"],
  "betting_score": 8.2
}}

CRITICAL RULES:
• If EV < 5% or confidence < 7.0: DO NOT INCLUDE
• "No bet" is valid — output [] if no edge exists
• Never chase losses or manufacture bets to fill the array
• Flag rotation matches — they are the highest EV opportunities
• Return ONLY the JSON array, no other text
"""
        response = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            candidates = json.loads(text)
            if not isinstance(candidates, list):
                return []
            # Enrich each candidate with institutional stake calculation
            for c in candidates:
                mp   = c.get("model_probability", 50) / 100
                mkt  = c.get("market_implied_probability", 50) / 100
                odds = c.get("odds", 2.0)
                sizing = institutional_stake(mp, odds, bankroll, mkt, peak_bankroll)
                # Override LLM stake with formula-derived one
                c["recommended_stake"]   = sizing["stake"]
                c["full_kelly_pct"]      = sizing["full_kelly_pct"]
                c["uncertainty_score"]   = sizing["uncertainty"]
                c["dd_adjustment"]       = sizing["dd_multiplier"]
            return candidates
        except json.JSONDecodeError:
            print(f"Betting Agent JSON parse error: {text[:200]}")
            return []

    async def reflect(self, bet: dict, won: bool) -> dict:
        """Post-settlement learning log — institutional self-reflection."""
        outcome = "WON" if won else "LOST"
        pnl = (bet['actual_stake'] * bet['actual_odds'] - bet['actual_stake']
               if won else -bet['actual_stake'])

        prompt = f"""
You are a self-learning quantitative sports model reviewing a settled position.
Analyse what happened and update your internal model accordingly.

SETTLED POSITION:
  Match:      {bet['match']}
  Selection:  {bet['selection']} @ {bet['actual_odds']}
  Stake:      €{bet['actual_stake']}
  P&L:        €{pnl:.2f}
  Outcome:    {outcome}
  Rec. odds:  {bet.get('rec_odds', 'N/A')}
  Model prob: {bet.get('model_probability', 'N/A')}%
  Uncertainty:{bet.get('uncertainty_score', 'N/A')}
  CLV outlook:{bet.get('clv_outlook', 'N/A')}

ORIGINAL REASONING: {bet.get('reasoning', 'N/A')}

Analyse using institutional self-review:
1. Was our probability estimate accurate post-match?
2. Did the bet beat the closing line? (CLV check)
3. Was stake sizing appropriate for the uncertainty level?
4. Was this a good process bet even if it lost / a bad process bet even if it won?
5. What should change in the next similar situation?

Return JSON only:
{{
  "date": "{datetime.now().strftime('%Y-%m-%d')}",
  "match": "{bet['match']}",
  "outcome": "{outcome}",
  "pnl": {pnl:.2f},
  "process_quality": "Good|Neutral|Poor",
  "clv_verdict": "Beat closing line|At closing line|Lost closing line|Unknown",
  "reflection": "honest 3-sentence analysis",
  "model_adjustment": "specific change to apply going forward",
  "uncertainty_calibration": "Model was: overconfident|calibrated|underconfident"
}}
"""
        response = await client.messages.create(
            model=MODEL, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except Exception:
            return {
                "date": datetime.now().strftime('%Y-%m-%d'),
                "match": bet['match'], "outcome": outcome, "pnl": pnl,
                "process_quality": "Neutral",
                "clv_verdict": "Unknown",
                "reflection": text[:300],
                "model_adjustment": "Review manually.",
                "uncertainty_calibration": "Unknown",
            }


# ──────────────────────────────────────────────────────────────
# AUDIT AGENT (Chief Risk Officer)
# ──────────────────────────────────────────────────────────────

class AuditAgent:

    async def review(self, recommendation: dict) -> dict:
        """
        Institutional-grade challenge of every bet recommendation.
        Applies 8 stress-test criteria before passing or rejecting.
        """
        prompt = f"""
You are the Chief Risk Officer (CRO) of a professional football trading desk.
Your role is NOT to approve — it is to CHALLENGE and STRESS-TEST.

You apply institutional risk standards. You are aware of:
• Fractional Kelly sizing: stakes should never exceed 40% of full Kelly
• Model uncertainty: high disagreement between model and market = reduce confidence
• CLV priority: is this a bet the market will confirm as value by kick-off?
• Drawdown discipline: losing streaks demand reduced aggression, not increased
• Portfolio correlation: does this add or duplicate existing exposure?

RECOMMENDATION TO AUDIT:
{json.dumps(recommendation, indent=2)}

━━━ 8-POINT INSTITUTIONAL STRESS TEST ━━━

1. PROBABILITY ACCURACY  — Is model_probability defensible? Any key news ignored?
2. MARKET EFFICIENCY     — Is this market genuinely soft or is it a sharp trap?
3. KELLY COMPLIANCE      — Does stake = bankroll × 0.40 × full_kelly × uncertainty_adj?
4. CLV OUTLOOK           — Will sharp money confirm our direction by kick-off?
5. CORRELATION RISK      — Does this overlap with other WC bets (same group/team/market)?
6. DRAWDOWN DISCIPLINE   — Given current portfolio state, is this bet appropriately sized?
7. INFORMATION QUALITY   — Is the intelligence (lineup, form, news) recent and reliable?
8. RECENCY BIAS CHECK    — Is this influenced by a single dramatic result that may not persist?

━━━ DECISION RULES ━━━
Score ≥ 8.0  AND  CLV = Positive/Neutral  AND  no major flags → "Approved"
Score 6.0–7.9  OR  CLV = Neutral  OR  1-2 minor concerns    → "Approved with Warnings"
Score < 6.0   OR  CLV = Negative  OR  any major flag          → "Rejected"

Return ONLY this JSON:
{{
  "score": 8.2,
  "status": "Approved|Approved with Warnings|Rejected",
  "clv_assessment": "Positive|Neutral|Negative",
  "kelly_compliance": "Compliant|Oversized|Undersized",
  "notes": "2-3 sentence honest audit summary",
  "warnings": ["specific warning 1", "specific warning 2"],
  "risk_flags": ["major red flag if any"],
  "approved_stake": 15
}}
"""
        response = await client.messages.create(
            model=MODEL, max_tokens=512,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except Exception:
            return {
                "score": 5.0, "status": "Approved with Warnings",
                "clv_assessment": "Neutral", "kelly_compliance": "Unknown",
                "notes": "Audit parse error — manual review recommended.",
                "warnings": ["Audit response could not be parsed"],
                "risk_flags": [], "approved_stake": recommendation.get("recommended_stake", 10),
            }

    async def build_accumulator(self, singles: list) -> list:
        """
        Correlation-penalised accumulator construction (Markowitz-inspired).
        Prefers cross-group, mixed-market combinations over same-type clusters.
        """
        if len(singles) < 2:
            return []

        prompt = f"""
You are building correlation-aware accumulators from approved singles.
Apply Markowitz portfolio logic: prefer LOW-CORRELATION leg combinations.

APPROVED SINGLES:
{json.dumps(singles, indent=2)}

━━━ CORRELATION MATRIX ━━━
High correlation (AVOID combining):
  • Two bets in the same tournament group (group results cluster)
  • Two Under 2.5 Goals bets on the same matchday (tournament style = correlated)
  • Bets involving the same team in different markets

Low correlation (PREFER):
  • 1X2 result from Group A + O/U 2.5 from Group G
  • Win bet from one group + qualification bet from another
  • Different match days

━━━ BUILD RULES ━━━
1. Conservative (2 legs):  target combined odds 2.00–3.50, stake €10–€15
2. Balanced (3 legs):      target 3.50–8.00, stake €7–€10
3. High Upside (4-5 legs): target 8.00+, stake €5–€7
4. NEVER combine legs with correlation > 0.4
5. At least one leg must be a 1X2 or DNB market (not all O/U)

Return JSON array:
[
  {{
    "type": "Conservative|Balanced|High Upside",
    "legs": ["Team A Win vs Team B", "Under 2.5 — Team C vs Team D"],
    "combined_odds": 2.55,
    "model_probability": 44.0,
    "recommended_stake": 10,
    "ev": 9.6,
    "correlation_score": 0.15,
    "correlation_notes": "Different groups, mixed markets — low correlation",
    "audit_score": 8.5,
    "audit_status": "Approved",
    "audit_notes": "Safe combination with genuine diversification."
  }}
]

Return ONLY the JSON array. Maximum 3 accumulators.
"""
        response = await client.messages.create(
            model=MODEL, max_tokens=1024,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            return json.loads(text)
        except Exception:
            return []


# ──────────────────────────────────────────────────────────────
# SINGLETONS
# ──────────────────────────────────────────────────────────────
betting_agent = BettingAgent()
audit_agent   = AuditAgent()
