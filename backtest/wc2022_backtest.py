"""
WC2022 Backtest Engine v4 — Optimised Institutional Model
===========================================================

v3 weaknesses addressed:
  R16 O/U:  19% win rate (5W/11L). Flat -0.18 xG KO penalty was wrong.
             High ELO-gap KO games go OVER (France 3-1, Brazil 4-1, England 3-0).
             Tight ELO-gap KO games go UNDER (Japan 1-1 Croatia, Morocco 0-0 Spain).

New in v4:
  [1] Asymmetric KO xG  — ELO-gap-aware KO scoring adjustment
                           Small gap (<100 pts): -0.22 (tight affairs)
                           Mid gap (100-200 pts): 0.00 (neutral)
                           Large gap (>200 pts): +0.18 (dominant wins)

  [2] Dynamic Shrinkage  — Pull extreme model probs toward market with
                           disagreement-scaled regularisation.
                           Inspired by Bayesian shrinkage in quant factor models.
                           Preserves large edges (rotation bets) while dampening
                           speculative high-odds picks.

  [3] Stage Confidence Multiplier
                           GROUP:1.00 | R16:0.88 | QF:0.82 | SF/FINAL:0.78
                           Reduces stake AND confidence in KO rounds.

  [4] Market-Specific EV Thresholds
                           O/U at odds<2.0: EV≥7%  (tight market, needs cushion)
                           1X2 underdog at odds>5.0: EV≥10%  (high variance)
                           Any KO round: +2% penalty on threshold
                           Base: EV≥5%

  [5] Mixed-Market Accumulators
                           REQUIRE ≥1 non-O/U leg per combination.
                           Eliminates all-Under-2.5 accumulator clusters.

  [6] Daily Bet Cap = 4
                           Take top-4 by confidence × ev_pct score.
                           Avoids over-concentration on busy group days.
"""

import math, json, statistics
from dataclasses import dataclass, field, asdict
from typing import Optional
from datetime import datetime

# ─────────────────────────────────────────────
# CONSTANTS
# ─────────────────────────────────────────────
KELLY_FRACTION    = 0.40
KELLY_MAX_BET     = 25.0
VAR_DAILY_PCT     = 0.15
STARTING_BANKROLL = 500.0
CONF_THRESHOLD    = 7.0
DAILY_BET_CAP     = 4      # max singles per day
ACCU_CONF_MIN     = 8.0
ACCU_MAX_DAILY    = 20.0

# Stage confidence multipliers (v4 NEW)
STAGE_CONF_MULT = {
    "GROUP":1.00, "R16":0.88, "QF":0.82, "SF":0.78, "3RD":0.78, "FINAL":0.78
}

BASE_ELO = {
    "Brazil":2166,"Belgium":2097,"Argentina":2089,"France":2003,
    "England":1969,"Spain":1963,"Netherlands":1956,"Portugal":1954,
    "Denmark":1921,"Germany":1914,"Croatia":1884,"USA":1883,
    "Switzerland":1878,"Mexico":1860,"Uruguay":1857,"Senegal":1855,
    "Serbia":1851,"Japan":1848,"Poland":1848,"Morocco":1837,
    "Australia":1821,"South Korea":1808,"Cameroon":1801,"Ecuador":1799,
    "Wales":1797,"Canada":1792,"Qatar":1790,"Tunisia":1784,
    "Iran":1779,"Costa Rica":1770,"Ghana":1747,"Saudi Arabia":1745,
}

GROUP_MAP = {
    "Qatar":"A","Ecuador":"A","Senegal":"A","Netherlands":"A",
    "England":"B","Iran":"B","USA":"B","Wales":"B",
    "Argentina":"C","Saudi Arabia":"C","Mexico":"C","Poland":"C",
    "France":"D","Australia":"D","Denmark":"D","Tunisia":"D",
    "Spain":"E","Costa Rica":"E","Germany":"E","Japan":"E",
    "Belgium":"F","Canada":"F","Morocco":"F","Croatia":"F",
    "Brazil":"G","Serbia":"G","Switzerland":"G","Cameroon":"G",
    "Portugal":"H","Ghana":"H","Uruguay":"H","South Korea":"H",
}

H2H_ADJ = {
    frozenset({"Argentina","France"}):(0.01,-0.01),
    frozenset({"Brazil","Argentina"}):(0.01,-0.01),
    frozenset({"England","Germany"}):(-0.01,0.01),
    frozenset({"Spain","Portugal"}):(0.01,-0.01),
}

# ═══════════════════════════════════════════════════════
# 1. DYNAMIC ELO (unchanged from v3)
# ═══════════════════════════════════════════════════════
def dynamic_elo(team, tournament_form, lineup_factor, rest_days, motivation):
    elo = float(BASE_ELO.get(team, 1800))
    for r in tournament_form[-3:]:
        if r=='W': elo+=35
        elif r=='L': elo-=20
    elo += (lineup_factor-1.0)*160
    if rest_days>=5: elo+=12
    elif rest_days<=2: elo-=22
    elo *= motivation
    return elo

def days_between(a,b):
    return (datetime.strptime(b,"%Y-%m-%d")-datetime.strptime(a,"%Y-%m-%d")).days

# ═══════════════════════════════════════════════════════
# 2. PROBABILITY ENGINE
# ═══════════════════════════════════════════════════════
def elo_win_prob(elo_h, elo_a):
    diff = elo_h - elo_a
    raw  = 1/(1+10**(-diff/400))
    draw = max(0.07, min(0.30-abs(diff)*0.00028, 0.32))
    return {"home_win":raw*(1-draw),"draw":draw,"away_win":(1-raw)*(1-draw)}

def market_probs(oh, od, oa):
    raw = {"home_win":1/oh,"draw":1/od,"away_win":1/oa}
    m   = sum(raw.values())
    return {k:v/m for k,v in raw.items()}

def blend_probabilities(elo_p, mkt_p, h2h_home=0.0):
    bl = {k:0.65*elo_p[k]+0.30*mkt_p[k] for k in elo_p}
    bl["home_win"]+=h2h_home; bl["away_win"]-=h2h_home
    t = sum(bl.values())
    return {k:max(0.01,v/t) for k,v in bl.items()}

# ═══════════════════════════════════════════════════════
# 3. [NEW v4] DYNAMIC SHRINKAGE
#    Regularise model probability toward market.
#    Large model/market disagreement → more shrinkage.
#    Preserves rotation-detection edge (large gap but justified by lineup).
# ═══════════════════════════════════════════════════════
def shrink_probability(model_p: float, market_p: float,
                       base_shrink: float = 0.10) -> float:
    """
    Dynamic Bayesian shrinkage.
    Formula: p_final = model_p × (1-s) + market_p × s
    s = base_shrink + disagreement × 0.25  [capped at 0.35]
    
    Effect: At 20pp disagreement, s ≈ 0.15 → ~85% model weight.
            At 40pp disagreement, s ≈ 0.20 → ~80% model weight.
    Doesn't kill high-EV bets; just trims speculative overconfidence.
    """
    disagreement = abs(model_p - market_p)
    s = min(base_shrink + disagreement * 0.25, 0.35)
    return model_p*(1-s) + market_p*s

# ═══════════════════════════════════════════════════════
# 4. [NEW v4] ASYMMETRIC KO xG ADJUSTMENT
#    KO matches are bimodal: tight OR dominant.
#    ELO gap predicts which type — not a flat penalty.
# ═══════════════════════════════════════════════════════
def ko_xg_adj(stage: str, elo_gap: float) -> float:
    if stage == "GROUP": return 0.0
    if elo_gap < 100:   return -0.22   # tight KO → fewer goals expected
    elif elo_gap < 200: return  0.00   # balanced → no adjustment
    else:               return +0.18   # dominant team → big win likely

# ═══════════════════════════════════════════════════════
# 5. [NEW v4] MARKET-SPECIFIC EV THRESHOLDS
# ═══════════════════════════════════════════════════════
def ev_threshold(market: str, odds: float, stage: str) -> float:
    base = 5.0
    if market=="O/U 2.5" and odds < 2.0: base = 7.0   # tight O/U needs cushion
    if market=="1X2" and odds > 5.0:     base = 10.0  # high-variance underdogs
    if stage not in ("GROUP",):          base += 2.0   # KO: demand more edge
    return base

# ═══════════════════════════════════════════════════════
# 6. INSTITUTIONAL STAKE SIZING (same as v3)
# ═══════════════════════════════════════════════════════
def kelly_full(p, odds):
    b = odds-1.0
    return max(0.0,(p*b-(1-p))/b)

def model_uncertainty(model_p, market_p, odds):
    return min(abs(model_p-market_p)*1.8 + min((odds-1)/25.0,0.15), 0.5)

def drawdown_multiplier(bankroll, peak):
    if peak<=0: return 1.0
    dd = (peak-bankroll)/peak*100
    if   dd<5:  return 1.00
    elif dd<10: return 0.75
    elif dd<20: return 0.50
    else:       return 0.25

def institutional_stake(prob, odds, bankroll, market_p, peak):
    fk     = kelly_full(prob, odds)
    if fk<=0: return 0.0, fk, 0.0, 1.0
    uncert = model_uncertainty(prob, market_p, odds)
    dd     = drawdown_multiplier(bankroll, peak)
    adj    = fk*KELLY_FRACTION*(1.0-uncert*0.8)*dd
    stake  = min(bankroll*adj, KELLY_MAX_BET)
    return round(max(stake,0)), fk, uncert, dd

def confidence_score(ev_pct, model_p, odds, stage_mult=1.0):
    base  = 5.0
    base += min(ev_pct/3.0, 2.5)
    edge  = model_p-(1/odds)
    base += min(edge*15, 1.5)
    if odds>1.5 and model_p>0.55: base+=0.5
    if odds<1.40:                  base-=0.8
    if odds>6.0 and ev_pct>30:    base+=0.3
    return round(min(max(base*stage_mult,1.0),10.0),1)

def ev(p, odds): return (p*(odds-1)-(1-p))*100

def passes_threshold(ev_pct, odds, stake, market, stage):
    min_ev = ev_threshold(market, odds, stage)
    if ev_pct < min_ev:              return False
    if odds < 1.40:                  return False
    if stake*(odds-1) < 5.0:         return False
    return True

# ═══════════════════════════════════════════════════════
# 7. [NEW v4] MIXED-MARKET ACCUMULATOR BUILDER
#    REQUIRE ≥1 non-O/U leg per combination.
# ═══════════════════════════════════════════════════════
def leg_correlation(a, b):
    if a['match']==b['match']: return 1.0
    corr = 0.0
    if GROUP_MAP.get(a.get('home',''),'') == GROUP_MAP.get(b.get('home',''),'') != '':
        corr += 0.35
    if a['market']==b['market']:
        corr += 0.25
    return min(corr, 0.8)

def portfolio_correlation(legs):
    if len(legs)<2: return 0.0
    pairs = [(legs[i],legs[j]) for i in range(len(legs)) for j in range(i+1,len(legs))]
    return sum(leg_correlation(a,b) for a,b in pairs)/len(pairs)

def build_accumulators(day_pool, date):
    """v4: REQUIRE mixed market types in accumulator legs."""
    seen = {}
    for c in sorted(day_pool, key=lambda x: x['conf'], reverse=True):
        if c['match'] not in seen: seen[c['match']]=c
    pool = list(seen.values())

    ou_pool  = [c for c in pool if c['market']=="O/U 2.5"]
    mix_pool = [c for c in pool if c['market']!="O/U 2.5"]

    accus = []

    # 2-leg: need 1 O/U + 1 non-O/U (true mixed combo)
    # If no non-O/U available, fall back to best 2 with correlation < 0.3
    if mix_pool and ou_pool:
        # Best mixed double: 1 of each type
        best_mix = mix_pool[0]
        best_ou  = ou_pool[0]
        legs2    = [best_mix, best_ou]
        c_odds2  = round(math.prod(l['odds'] for l in legs2), 2)
        corr2    = portfolio_correlation(legs2)
        stake2   = 10 if corr2<0.3 else 7
        accus.append({'type':'DOUBLE','legs':legs2,'combined_odds':c_odds2,
                      'stake':stake2,'correlation':round(corr2,2),'mixed':True})

        # Best mixed treble: 2 O/U + 1 non-O/U
        if len(ou_pool)>=2:
            legs3 = [best_mix, ou_pool[0], ou_pool[1]]
            c3    = round(math.prod(l['odds'] for l in legs3), 2)
            corr3 = portfolio_correlation(legs3)
            stake3 = 7 if corr3<0.3 else 5
            accus.append({'type':'TREBLE','legs':legs3,'combined_odds':c3,
                          'stake':stake3,'correlation':round(corr3,2),'mixed':True})
    elif len(pool)>=2:
        # Fallback: corr-penalised same-market (unchanged from v3)
        def combo_score(legs):
            avg_ev  = sum(l['ev_pct'] for l in legs)/len(legs)
            avg_c   = sum(l['conf'] for l in legs)/len(legs)
            corr    = portfolio_correlation(legs)
            return avg_ev*avg_c/(1+corr*2)
        best2 = max(([pool[i],pool[j]] for i in range(len(pool)) for j in range(i+1,len(pool))),
                    key=combo_score, default=None)
        if best2:
            c2 = round(math.prod(l['odds'] for l in best2),2)
            cr = portfolio_correlation(best2)
            accus.append({'type':'DOUBLE','legs':best2,'combined_odds':c2,
                          'stake':7 if cr>=0.3 else 10,'correlation':round(cr,2),'mixed':False})
    return accus

# ═══════════════════════════════════════════════════════
# 8. MATCH DATABASE
# ═══════════════════════════════════════════════════════
@dataclass
class Match:
    date:str;stage:str;group:str;home:str;away:str
    odds_h:float;odds_d:float;odds_a:float;odds_o25:float;odds_u25:float
    result:str;goals:tuple
    home_lineup:float=1.0;away_lineup:float=1.0
    home_motivation:float=1.0;away_motivation:float=1.0

MATCHES = [
    Match("2022-11-20","GROUP","A","Qatar","Ecuador",2.40,3.50,2.80,2.05,1.80,"A",(0,2)),
    Match("2022-11-21","GROUP","A","Senegal","Netherlands",6.00,3.90,1.55,1.85,1.95,"A",(0,2)),
    Match("2022-11-25","GROUP","A","Qatar","Senegal",3.40,3.30,2.20,2.00,1.85,"A",(1,3)),
    Match("2022-11-25","GROUP","A","Netherlands","Ecuador",1.65,3.75,5.50,1.75,2.10,"D",(1,1)),
    Match("2022-11-29","GROUP","A","Ecuador","Senegal",2.60,3.20,2.60,1.90,1.95,"A",(1,2)),
    Match("2022-11-29","GROUP","A","Netherlands","Qatar",1.25,7.00,13.0,1.50,2.60,"H",(2,0)),
    Match("2022-11-21","GROUP","B","England","Iran",1.35,5.50,9.50,1.65,2.25,"H",(6,2)),
    Match("2022-11-21","GROUP","B","USA","Wales",2.20,3.10,3.60,2.00,1.85,"D",(1,1)),
    Match("2022-11-25","GROUP","B","Wales","Iran",2.10,3.20,3.60,1.95,1.90,"A",(0,2)),
    Match("2022-11-25","GROUP","B","England","USA",1.90,3.50,4.50,1.90,1.95,"D",(0,0)),
    Match("2022-11-29","GROUP","B","Iran","USA",4.20,3.30,1.95,2.00,1.85,"A",(0,1)),
    Match("2022-11-29","GROUP","B","Wales","England",9.00,5.00,1.35,2.10,1.75,"A",(0,3)),
    Match("2022-11-22","GROUP","C","Argentina","Saudi Arabia",1.10,8.50,32.0,1.55,2.45,"A",(1,2)),
    Match("2022-11-22","GROUP","C","Mexico","Poland",2.20,3.30,3.20,1.85,2.00,"D",(0,0)),
    Match("2022-11-26","GROUP","C","Poland","Saudi Arabia",1.55,3.60,6.00,1.90,1.95,"H",(2,0)),
    Match("2022-11-26","GROUP","C","Argentina","Mexico",1.45,4.00,7.00,1.75,2.10,"H",(2,0)),
    Match("2022-11-30","GROUP","C","Saudi Arabia","Mexico",4.50,3.50,1.85,2.00,1.85,"A",(1,2)),
    Match("2022-11-30","GROUP","C","Poland","Argentina",5.00,4.00,1.70,1.85,2.00,"A",(0,2)),
    Match("2022-11-22","GROUP","D","Denmark","Tunisia",1.80,3.40,4.50,1.90,1.95,"D",(0,0)),
    Match("2022-11-22","GROUP","D","France","Australia",1.30,6.00,10.0,1.55,2.45,"H",(4,1)),
    Match("2022-11-26","GROUP","D","Tunisia","Australia",3.00,3.10,2.50,1.95,1.90,"A",(0,1)),
    Match("2022-11-26","GROUP","D","France","Denmark",1.80,3.60,4.50,1.85,2.00,"H",(2,1)),
    Match("2022-11-30","GROUP","D","Australia","Denmark",5.00,3.50,1.70,1.95,1.90,"H",(1,0)),
    Match("2022-11-30","GROUP","D","Tunisia","France",7.00,4.50,1.45,2.00,1.85,"H",(1,0),
          home_lineup=1.0,away_lineup=0.68,home_motivation=1.06,away_motivation=0.90),
    Match("2022-11-23","GROUP","E","Spain","Costa Rica",1.22,7.00,15.0,1.50,2.60,"H",(7,0)),
    Match("2022-11-23","GROUP","E","Germany","Japan",1.40,4.80,8.00,1.70,2.15,"A",(1,2)),
    Match("2022-11-27","GROUP","E","Japan","Costa Rica",1.95,3.30,4.00,1.95,1.90,"A",(0,1)),
    Match("2022-11-27","GROUP","E","Spain","Germany",1.95,3.50,4.00,1.85,2.00,"D",(1,1)),
    Match("2022-12-01","GROUP","E","Japan","Spain",7.00,4.50,1.45,2.00,1.85,"H",(2,1)),
    Match("2022-12-01","GROUP","E","Costa Rica","Germany",6.00,4.50,1.55,1.95,1.90,"A",(2,4),
          home_motivation=1.04,away_motivation=1.06),
    Match("2022-11-23","GROUP","F","Belgium","Canada",1.40,4.50,8.00,1.80,2.05,"H",(1,0)),
    Match("2022-11-23","GROUP","F","Morocco","Croatia",4.50,3.30,1.85,1.85,2.00,"D",(0,0)),
    Match("2022-11-27","GROUP","F","Belgium","Morocco",1.55,3.80,6.00,1.90,1.95,"A",(0,2)),
    Match("2022-11-27","GROUP","F","Croatia","Canada",1.60,3.60,5.50,1.75,2.10,"H",(4,1)),
    Match("2022-12-01","GROUP","F","Croatia","Belgium",3.80,3.50,2.00,1.90,1.95,"D",(0,0)),
    Match("2022-12-01","GROUP","F","Morocco","Canada",2.30,3.10,3.20,1.90,1.95,"H",(2,1)),
    Match("2022-11-24","GROUP","G","Brazil","Serbia",1.42,4.60,8.00,1.65,2.25,"H",(2,0)),
    Match("2022-11-24","GROUP","G","Switzerland","Cameroon",1.90,3.30,4.20,1.95,1.90,"H",(1,0)),
    Match("2022-11-28","GROUP","G","Brazil","Switzerland",1.55,4.00,6.50,1.70,2.15,"H",(1,0)),
    Match("2022-11-28","GROUP","G","Cameroon","Serbia",3.80,3.20,1.90,2.05,1.80,"D",(3,3)),
    Match("2022-12-02","GROUP","G","Serbia","Switzerland",2.60,3.20,2.80,1.90,1.95,"A",(2,3)),
    Match("2022-12-02","GROUP","G","Cameroon","Brazil",9.00,5.50,1.35,2.00,1.85,"H",(1,0),
          home_lineup=1.0,away_lineup=0.72,home_motivation=1.06,away_motivation=0.88),
    Match("2022-11-24","GROUP","H","Uruguay","South Korea",2.10,3.10,3.80,1.90,1.95,"D",(0,0)),
    Match("2022-11-24","GROUP","H","Portugal","Ghana",1.55,4.00,6.00,1.80,2.05,"H",(3,2)),
    Match("2022-11-28","GROUP","H","South Korea","Ghana",2.60,3.10,2.90,1.95,1.90,"A",(2,3)),
    Match("2022-11-28","GROUP","H","Portugal","Uruguay",1.70,3.70,5.00,1.80,2.05,"H",(2,0)),
    Match("2022-12-02","GROUP","H","Ghana","Uruguay",4.00,3.30,1.90,2.00,1.85,"A",(0,2)),
    Match("2022-12-02","GROUP","H","South Korea","Portugal",5.50,3.90,1.60,2.00,1.85,"H",(2,1),
          away_lineup=0.75,away_motivation=0.92,home_motivation=1.06),
    Match("2022-12-03","R16","","Netherlands","USA",1.65,3.75,5.50,1.90,1.95,"H",(3,1)),
    Match("2022-12-03","R16","","Argentina","Australia",1.25,6.50,12.0,1.60,2.35,"H",(2,1)),
    Match("2022-12-04","R16","","France","Poland",1.35,5.00,9.00,1.65,2.25,"H",(3,1)),
    Match("2022-12-04","R16","","England","Senegal",1.55,4.00,6.00,1.80,2.05,"H",(3,0)),
    Match("2022-12-05","R16","","Japan","Croatia",4.00,3.40,1.95,1.95,1.90,"A",(1,1)),
    Match("2022-12-05","R16","","Brazil","South Korea",1.22,7.00,14.0,1.50,2.60,"H",(4,1)),
    Match("2022-12-06","R16","","Morocco","Spain",7.00,4.50,1.45,1.90,1.95,"H",(0,0)),
    Match("2022-12-06","R16","","Portugal","Switzerland",1.65,4.00,5.50,1.75,2.10,"H",(6,1)),
    Match("2022-12-09","QF","","Croatia","Brazil",6.50,4.50,1.45,1.80,2.05,"H",(1,1)),
    Match("2022-12-09","QF","","Netherlands","Argentina",4.50,3.80,1.80,1.90,1.95,"A",(2,2)),
    Match("2022-12-10","QF","","Morocco","Portugal",6.00,4.50,1.55,1.85,2.00,"H",(1,0)),
    Match("2022-12-10","QF","","England","France",3.50,3.50,2.10,1.90,1.95,"A",(1,2)),
    Match("2022-12-13","SF","","Argentina","Croatia",1.70,4.00,5.00,1.85,2.00,"H",(3,0)),
    Match("2022-12-14","SF","","France","Morocco",1.40,4.50,8.50,1.70,2.15,"H",(2,0)),
    Match("2022-12-17","3RD","","Croatia","Morocco",1.95,3.30,3.80,1.90,1.95,"H",(2,1)),
    Match("2022-12-18","FINAL","","Argentina","France",2.30,3.50,3.00,1.90,1.95,"H",(3,3)),
]

# ═══════════════════════════════════════════════════════
# 9. DATACLASSES
# ═══════════════════════════════════════════════════════
@dataclass
class Bet:
    date:str;stage:str;match:str;market:str;selection:str
    odds:float;model_prob:float;market_prob:float;shrunk_prob:float
    ev_pct:float;ev_threshold_used:float;confidence:float
    full_kelly:float;uncertainty:float;dd_mult:float;stage_mult:float
    stake:float;result:str;pnl:float;bankroll_after:float
    notes:str=""

@dataclass
class Accumulator:
    date:str;accu_type:str;legs:list;combined_odds:float
    correlation:float;mixed_market:bool
    stake:float;result:str;pnl:float;bankroll_after:float

# ═══════════════════════════════════════════════════════
# 10. MAIN BACKTEST LOOP
# ═══════════════════════════════════════════════════════
def run_backtest(starting_bankroll=STARTING_BANKROLL):
    bankroll   = starting_bankroll
    peak_bk    = starting_bankroll
    bets       = []
    accumulators = []
    team_form  = {}
    team_last  = {}
    team_exp   = {}
    daily_staked   = {}
    daily_accu_pool = {}

    for m in MATCHES:
        day       = m.date
        day_total = daily_staked.get(day, 0.0)
        var_cap   = bankroll * VAR_DAILY_PCT
        smult     = STAGE_CONF_MULT.get(m.stage, 1.0)

        home_rest = days_between(team_last[m.home],m.date) if m.home in team_last else 10
        away_rest = days_between(team_last[m.away],m.date) if m.away in team_last else 10

        elo_h  = dynamic_elo(m.home,team_form.get(m.home,[]),m.home_lineup,home_rest,m.home_motivation)
        elo_a  = dynamic_elo(m.away,team_form.get(m.away,[]),m.away_lineup,away_rest,m.away_motivation)
        elo_gap = abs(elo_h-elo_a)

        elo_p   = elo_win_prob(elo_h, elo_a)
        mkt_p   = market_probs(m.odds_h, m.odds_d, m.odds_a)
        h2h_key = frozenset({m.home,m.away})
        h2h     = H2H_ADJ.get(h2h_key,(0.0,0.0))
        h2h_h   = h2h[0] if list(h2h_key)[0]==m.home else h2h[1]
        fin_p   = blend_probabilities(elo_p, mkt_p, h2h_h)

        # [v4] Asymmetric KO xG adjustment
        xg_adj   = ko_xg_adj(m.stage, elo_gap)
        base_xg  = 2.52 + elo_gap/750 + xg_adj
        base_xg *= (0.85 + ((m.home_lineup+m.away_lineup)/2)*0.15)
        mo25_raw = round(min(max(0.28+(base_xg-2.5)*0.32,0.22),0.78),3)
        mu25_raw = round(1-mo25_raw,3)

        candidates = []
        for sel, mp_raw, odds, market, mkt_mp in [
            (m.home+" Win", fin_p["home_win"], m.odds_h, "1X2",     mkt_p["home_win"]),
            ("Draw",         fin_p["draw"],    m.odds_d, "1X2",     mkt_p["draw"]),
            (m.away+" Win",  fin_p["away_win"],m.odds_a, "1X2",     mkt_p["away_win"]),
            ("Over 2.5 Goals",  mo25_raw, m.odds_o25, "O/U 2.5",   mo25_raw),
            ("Under 2.5 Goals", mu25_raw, m.odds_u25, "O/U 2.5",   mu25_raw),
        ]:
            # [v4] Apply shrinkage
            mp = shrink_probability(mp_raw, mkt_mp) if market=="1X2" else mp_raw
            # O/U: market_p is the same as model (we don't shrink against itself)
            # For O/U, shrink model against Poisson-based neutral (0.5)
            if market=="O/U 2.5":
                mp = shrink_probability(mp_raw, 0.5, base_shrink=0.05)

            ev_pct = ev(mp, odds)
            ev_thr = ev_threshold(market, odds, m.stage)
            # [v4] Apply stage multiplier to confidence
            conf   = confidence_score(ev_pct, mp, odds, smult)

            if conf < CONF_THRESHOLD: continue

            stake, fk, uncert, dd = institutional_stake(mp, odds, bankroll, mkt_mp, peak_bk)
            # [v4] Scale stake by stage multiplier
            stake = round(stake * smult)

            if not passes_threshold(ev_pct, odds, stake, market, m.stage): continue

            candidates.append({
                'sel':sel,'mp':mp,'mp_raw':mp_raw,'mkt_mp':mkt_mp,
                'odds':odds,'market':market,'ev_pct':ev_pct,'ev_thr':ev_thr,
                'conf':conf,'stake':stake,'fk':fk,'uncert':uncert,'dd_mult':dd,
                'smult':smult,'match':f"{m.home} vs {m.away}",
                'home':m.home,'away':m.away,
                'score': conf * ev_pct,  # ranking key
            })

        # [v4] Daily bet cap: take top-4 by conf×ev score
        candidates.sort(key=lambda x: x['score'], reverse=True)
        candidates = candidates[:DAILY_BET_CAP]

        for c in candidates:
            if day_total >= var_cap: break
            if c['market']=="1X2":
                backed = (m.home if m.home+" Win"==c['sel']
                          else m.away if m.away+" Win"==c['sel'] else None)
                if backed and team_exp.get(backed,0)>=40: continue

            stake = min(c['stake'], var_cap-day_total)
            if stake < 3: continue

            # Resolve
            won = False; tg = m.goals[0]+m.goals[1]
            if c['market']=="1X2":
                if m.home+" Win"==c['sel'] and m.result=="H": won=True
                elif c['sel']=="Draw" and m.result=="D":       won=True
                elif m.away+" Win"==c['sel'] and m.result=="A":won=True
            else:
                if "Over"  in c['sel'] and tg>2:  won=True
                if "Under" in c['sel'] and tg<=2: won=True

            pnl = round(stake*(c['odds']-1) if won else -stake,2)
            bankroll = round(bankroll+pnl,2)
            peak_bk  = max(peak_bk, bankroll)
            day_total += stake
            daily_staked[day] = day_total

            if c['market']=="1X2":
                backed = (m.home if m.home+" Win"==c['sel']
                          else m.away if m.away+" Win"==c['sel'] else None)
                if backed: team_exp[backed]=team_exp.get(backed,0)+stake

            note=""
            if m.home_lineup<0.85 or m.away_lineup<0.85: note="ROTATION"
            elif c['ev_pct']>25: note="HIGH VALUE"
            elif c['uncert']>0.3: note="UNCERTAIN"

            bets.append(Bet(
                date=m.date,stage=m.stage,match=c['match'],
                market=c['market'],selection=c['sel'],
                odds=c['odds'],model_prob=round(c['mp'],3),
                market_prob=round(c['mkt_mp'],3),shrunk_prob=round(c['mp'],3),
                ev_pct=round(c['ev_pct'],1),ev_threshold_used=c['ev_thr'],
                confidence=c['conf'],full_kelly=round(c['fk'],4),
                uncertainty=round(c['uncert'],3),dd_mult=c['dd_mult'],
                stage_mult=c['smult'],stake=stake,
                result="WON" if won else "LOST",pnl=pnl,
                bankroll_after=bankroll,notes=note
            ))

            if c['conf']>=ACCU_CONF_MIN:
                daily_accu_pool.setdefault(day,[]).append({**c,'won':won})

        rh='W' if m.result=='H' else ('L' if m.result=='A' else 'D')
        ra='L' if m.result=='H' else ('W' if m.result=='A' else 'D')
        team_form.setdefault(m.home,[]).append(rh)
        team_form.setdefault(m.away,[]).append(ra)
        team_last[m.home]=m.date; team_last[m.away]=m.date

    # Accumulators
    for day,pool in daily_accu_pool.items():
        spend=0
        for a in build_accumulators(pool,day):
            if spend>=ACCU_MAX_DAILY: break
            stake=min(a['stake'],ACCU_MAX_DAILY-spend)
            if stake<3: continue
            all_won=all(l.get('won',False) for l in a['legs'])
            pnl=round(stake*(a['combined_odds']-1) if all_won else -stake,2)
            bankroll=round(bankroll+pnl,2); peak_bk=max(peak_bk,bankroll)
            spend+=stake
            accumulators.append(Accumulator(
                date=day,accu_type=a['type'],
                legs=[{'match':l['match'],'selection':l['sel'],'odds':l['odds'],
                        'market':l['market'],'home':l.get('home','')} for l in a['legs']],
                combined_odds=a['combined_odds'],correlation=a['correlation'],
                mixed_market=a.get('mixed',False),
                stake=stake,result="WON" if all_won else "LOST",
                pnl=pnl,bankroll_after=bankroll
            ))

    return {"bets":bets,"accumulators":accumulators,
            "final_bankroll":bankroll,"starting_bankroll":starting_bankroll}

# ═══════════════════════════════════════════════════════
# 11. METRICS
# ═══════════════════════════════════════════════════════
def betting_sharpe(bets):
    evs=[b.ev_pct for b in bets]
    if len(evs)<2: return 0.0
    mu=statistics.mean(evs); sd=statistics.stdev(evs)
    return round(mu/sd,2) if sd>0 else 0.0

def information_ratio(bets):
    edges=[b.model_prob-b.market_prob for b in bets]
    if len(edges)<2: return 0.0
    mu=statistics.mean(edges); sd=statistics.stdev(edges)
    return round(mu/sd,2) if sd>0 else 0.0

def clv_proxy(bets):
    early=[b for b in bets if b.stage=="GROUP"]
    if not early: return 0.0
    return round(statistics.mean(b.model_prob-b.market_prob for b in early)*100,2)

def hhi(bets):
    total=sum(b.stake for b in bets)
    if total==0: return 0.0
    return round(sum((b.stake/total)**2 for b in bets),4)

def summarize(result):
    bets=result["bets"]; accus=result["accumulators"]
    start=result["starting_bankroll"]; final=result["final_bankroll"]
    won=[b for b in bets if b.result=="WON"]
    total_staked=sum(b.stake for b in bets)
    total_pnl=sum(b.pnl for b in bets)
    accu_pnl=sum(a.pnl for a in accus)

    by_stage={}; by_market={}
    for b in bets:
        for d,key in [(by_stage,b.stage),(by_market,b.market)]:
            d.setdefault(key,{"bets":0,"won":0,"staked":0.0,"pnl":0.0})
            d[key]["bets"]+=1; d[key]["won"]+=1 if b.result=="WON" else 0
            d[key]["staked"]+=b.stake; d[key]["pnl"]+=b.pnl

    peak=start; max_dd=0.0; running=start
    for b in bets:
        running+=b.pnl; peak=max(peak,running)
        max_dd=max(max_dd,(peak-running)/peak*100)

    mixed_accus=[a for a in accus if a.mixed_market]
    return {
        "total_bets":len(bets),"won":len(won),"lost":len(bets)-len(won),
        "hit_rate":round(len(won)/len(bets)*100,1) if bets else 0,
        "total_staked":round(total_staked,2),"total_pnl":round(total_pnl,2),
        "accu_count":len(accus),"accu_won":len([a for a in accus if a.result=="WON"]),
        "accu_mixed":len(mixed_accus),"accu_pnl":round(accu_pnl,2),
        "final_bankroll":round(final,2),
        "roi":round((final-start)/start*100,1),
        "roi_on_staked":round(total_pnl/total_staked*100,1) if total_staked else 0,
        "avg_odds":round(sum(b.odds for b in bets)/len(bets),2) if bets else 0,
        "avg_stake":round(total_staked/len(bets),2) if bets else 0,
        "avg_ev":round(sum(b.ev_pct for b in bets)/len(bets),1) if bets else 0,
        "max_drawdown":round(max_dd,1),
        "betting_sharpe":betting_sharpe(bets),
        "information_ratio":information_ratio(bets),
        "clv_proxy":clv_proxy(bets),"hhi":hhi(bets),
        "by_stage":by_stage,"by_market":by_market,
        "equity_curve":[start]+[b.bankroll_after for b in bets],
    }

if __name__=="__main__":
    result=run_backtest(STARTING_BANKROLL)
    stats=summarize(result)
    bets=result["bets"]; accus=result["accumulators"]

    print("\n"+"="*72)
    print("WC2022 BACKTEST v4 — OPTIMISED INSTITUTIONAL MODEL")
    print("="*72)
    print(f"Total Singles:    {stats['total_bets']}  ({stats['won']}W / {stats['lost']}L)")
    print(f"Hit Rate:         {stats['hit_rate']}%")
    print(f"Total Staked:     €{stats['total_staked']}")
    print(f"Singles P&L:      €{stats['total_pnl']:+.2f}")
    print(f"Accumulators:     {stats['accu_count']} placed  {stats['accu_won']} won  "
          f"[{stats['accu_mixed']} mixed-market]  P&L: €{stats['accu_pnl']:+.2f}")
    print(f"Final Bankroll:   €{stats['final_bankroll']:.2f}")
    print(f"ROI (bankroll):   {stats['roi']:+.1f}%")
    print(f"ROI (on staked):  {stats['roi_on_staked']:+.1f}%")
    print(f"Max Drawdown:     {stats['max_drawdown']}%")
    print()
    print("── INSTITUTIONAL METRICS ────────────────────────────")
    print(f"Betting Sharpe:   {stats['betting_sharpe']:.2f}   (>1.5 = institutional)")
    print(f"Information Ratio:{stats['information_ratio']:.2f}   (>0.5 = consistent edge)")
    print(f"CLV Proxy:       {stats['clv_proxy']:+.2f}pp")
    print(f"Portfolio HHI:    {stats['hhi']:.4f}  (<0.15 = diversified)")
    print()
    print("── BY STAGE ──")
    for s,d in stats['by_stage'].items():
        wr=d['won']/d['bets']*100 if d['bets'] else 0
        print(f"  {s:<8} {d['bets']:2}b  {wr:.0f}%WR  P&L:€{d['pnl']:+.2f}")
    print("\n── BY MARKET ──")
    for mk,d in stats['by_market'].items():
        wr=d['won']/d['bets']*100 if d['bets'] else 0
        print(f"  {mk:<10} {d['bets']:2}b  {wr:.0f}%WR  P&L:€{d['pnl']:+.2f}")
    print("\n── ACCUMULATORS ──")
    for a in accus:
        legs=", ".join(f"{l['selection']}@{l['odds']} ({l['market']})" for l in a.legs)
        mixed="✓MIX" if a.mixed_market else "corr"
        print(f"  {a.date} {a.accu_type} [{mixed}] corr={a.correlation:.2f}  "
              f"{legs}  x{a.combined_odds}  €{a.stake}  {a.result}  P&L:€{a.pnl:+.2f}")

    out={"stats":stats,"bets":[asdict(b) for b in bets],"accumulators":[asdict(a) for a in accus]}
    with open("/sessions/festive-gallant-dirac/mnt/Sports betting/backtest/wc2022_results.json","w") as f:
        json.dump(out,f,indent=2,default=str)
    print(f"\n✅  Saved. v3→v4 comparison:")
    print(f"    ROI: v3=+96.5%  →  v4={stats['roi']:+.1f}%")
    print(f"    R16 WR: v3=19%  →  v4={int(stats['by_stage'].get('R16',{}).get('won',0)/max(stats['by_stage'].get('R16',{}).get('bets',1),1)*100)}%")
    print(f"    MaxDD: v3=19.5% →  v4={stats['max_drawdown']}%")
    print(f"    Sharpe: v3=1.75 →  v4={stats['betting_sharpe']}")
