"""
WC2026 Dual-Agent System
─────────────────────────
Betting Agent:  Finds edge, generates trade recommendations
Audit Agent:    Chief Risk Officer — challenges every bet, can reject

Both use Claude API via anthropic SDK.
"""

import os, json
from datetime import datetime
from anthropic import AsyncAnthropic

client = AsyncAnthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))
MODEL  = "claude-opus-4-6"   # Use claude-haiku-4-5-20251001 to reduce costs

# ──────────────────────────────────────────────────────────────
# BETTING AGENT
# ──────────────────────────────────────────────────────────────

class BettingAgent:

    async def analyze(self, matches: list, odds: list, news: list) -> list:
        """
        Given live data, generate betting recommendations.
        Returns a list of candidate recommendations.
        """
        prompt = f"""
You are an elite football betting analyst for the 2026 FIFA World Cup.

LIVE DATA (as of {datetime.now().strftime('%Y-%m-%d %H:%M UTC')}):

MATCHES:
{json.dumps(matches[:20], indent=2)}

CURRENT ODDS (Betclic France):
{json.dumps(odds[:30], indent=2)}

BREAKING NEWS:
{json.dumps(news[:10], indent=2)}

Your task:
1. Identify 2–5 high-value betting opportunities with POSITIVE expected value.
2. Only recommend markets commonly available on Betclic France (1X2, DNB, O/U goals, BTTS, Group Winner, Qualification, Asian Handicap, Tournament Winner).
3. Calculate model probability vs market implied probability.
4. CRITICAL: If no edge exists (EV < 3%), output NO recommendations. A "no bet" decision is valid and professional.
5. Consider injuries, form, correlation between bets, and market efficiency.

Return a JSON array. Each item must have these exact fields:
{{
  "match": "Team A vs Team B",
  "date": "YYYY-MM-DD",
  "market": "market type",
  "selection": "what to bet on",
  "odds": 1.75,
  "fair_odds": 1.60,
  "model_probability": 62.5,
  "market_implied_probability": 57.1,
  "edge": 5.4,
  "ev": 9.4,
  "confidence": 7.5,
  "risk": "Low|Moderate|High",
  "recommended_stake": 15,
  "tier": 1,
  "rationale": "detailed reasoning...",
  "factors": ["factor 1", "factor 2"],
  "risks": ["risk 1", "risk 2"],
  "betting_score": 8.2
}}

Stake sizing rules (bankroll = €500):
- Confidence 9–10: max €25 (5%)
- Confidence 7–8: max €15 (3%)
- Confidence 5–6: max €10 (2%)
- Below 5: don't recommend

Return ONLY the JSON array. No other text.
If no valid opportunities exist, return an empty array: []
"""
        response = await client.messages.create(
            model=MODEL,
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text.strip()
        # Extract JSON from response (handle markdown code blocks)
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        try:
            candidates = json.loads(text)
            return candidates if isinstance(candidates, list) else []
        except json.JSONDecodeError:
            print(f"Betting Agent JSON parse error: {text[:200]}")
            return []

    async def reflect(self, bet: dict, won: bool) -> dict:
        """After a bet settles, generate a learning log entry."""
        outcome = "WON" if won else "LOST"
        prompt = f"""
You are a self-learning sports trading model reviewing a settled bet.

SETTLED BET:
Match: {bet['match']}
Selection: {bet['selection']}
Market: {bet['market']}
Recommended odds: {bet.get('rec_odds', 'N/A')}
Actual odds: {bet['actual_odds']}
Stake: €{bet['actual_stake']}
Outcome: {outcome}
P&L: €{bet['actual_stake'] * bet['actual_odds'] - bet['actual_stake'] if won else -bet['actual_stake']:.2f}

Original reasoning: {bet.get('reasoning', 'N/A')}

Write a brief (3–5 sentence) model self-reflection:
1. What was correct in the analysis?
2. What was incorrect or overlooked?
3. What should the model adjust in future recommendations?

Be honest and specific. No fluff.

Return as JSON:
{{
  "date": "{datetime.now().strftime('%Y-%m-%d')}",
  "match": "{bet['match']}",
  "outcome": "{outcome}",
  "reflection": "your honest reflection here",
  "adjustment": "what to change in future model"
}}
"""
        response = await client.messages.create(
            model=MODEL,
            max_tokens=512,
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
            return {"date": datetime.now().strftime('%Y-%m-%d'), "match": bet['match'],
                    "outcome": outcome, "reflection": text[:300], "adjustment": "Review manually."}


# ──────────────────────────────────────────────────────────────
# AUDIT AGENT (Chief Risk Officer)
# ──────────────────────────────────────────────────────────────

class AuditAgent:

    async def review(self, recommendation: dict) -> dict:
        """
        Challenge a recommendation from the Betting Agent.
        Returns audit verdict: score, status, notes, warnings.
        """
        prompt = f"""
You are the Chief Risk Officer (Audit Agent) for a professional sports trading desk.

Your ONLY job is to CHALLENGE the following bet recommendation. You are NOT trying to approve it —
you are stress-testing it. Be skeptical. Be rigorous.

RECOMMENDATION TO AUDIT:
{json.dumps(recommendation, indent=2)}

Check for:
1. Data quality — are the facts accurate and current?
2. Overconfidence — is the confidence score justified?
3. Correlation risk — does this bet overlap with others?
4. Market efficiency — is this a genuinely soft market or a trap?
5. Hidden risks — injuries? team news? weather? motivation?
6. Stake size — is the recommended stake appropriate for this risk level?
7. EV accuracy — does the EV calculation hold up?
8. Recency bias — is this influenced by recent performance that may not persist?

DECISION RULES:
- Score ≥ 8.0 AND no major red flags → "Approved"
- Score 6.0–7.9 OR minor concerns → "Approved with Warnings"
- Score < 6.0 OR major red flag detected → "Rejected"

Return ONLY this JSON:
{{
  "score": 8.2,
  "status": "Approved|Approved with Warnings|Rejected",
  "notes": "Clear, honest audit summary (2-3 sentences)",
  "warnings": ["specific warning 1", "specific warning 2"],
  "risk_flags": []
}}
"""
        response = await client.messages.create(
            model=MODEL,
            max_tokens=512,
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
            return {"score": 5.0, "status": "Approved with Warnings",
                    "notes": "Audit parse error — manual review recommended.",
                    "warnings": ["Audit response could not be parsed"],
                    "risk_flags": []}

    async def build_accumulator(self, singles: list) -> list:
        """
        Given a list of approved single bets, build 1–3 correlation-safe accumulators.
        """
        if len(singles) < 2:
            return []

        prompt = f"""
You are building accumulators from these approved single bets:
{json.dumps(singles, indent=2)}

Rules:
1. Check correlation: never combine bets where one outcome depends on another
   (e.g., don't combine "France Win Group" with "France Beat Iraq" — correlated)
2. Build max 3 accumulators:
   - Conservative: 2 legs, target combined odds 2.00–3.50
   - Balanced: 3 legs, target 3.50–8.00
   - High Upside: 4–5 legs, target 8.00+
3. Each accumulator must state correlation risk level: Low/Moderate/High
4. Only use Low or Moderate correlation — reject High

Return JSON array:
[
  {{
    "type": "Conservative",
    "legs": ["match1 — selection1", "match2 — selection2"],
    "combined_odds": 2.55,
    "model_probability": 44,
    "recommended_stake": 15,
    "ev": 9.6,
    "correlation": "Low — different groups, independent outcomes",
    "audit_score": 8.5,
    "audit_status": "Approved",
    "audit_notes": "Safe combination. Low correlation verified."
  }}
]
"""
        response = await client.messages.create(
            model=MODEL,
            max_tokens=1024,
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


# Singletons
betting_agent = BettingAgent()
audit_agent   = AuditAgent()
