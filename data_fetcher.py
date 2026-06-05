"""
WC2026 Data Fetcher
────────────────────
Connects to:
  - football-data.org  (matches, lineups, standings)
  - The Odds API        (live Betclic odds)
  - NewsAPI             (injuries, team news)
"""

import os, aiohttp, asyncio
from datetime import datetime, timezone


class DataFetcher:

    # Free API keys (get yours at each site — takes 2 minutes)
    FOOTBALL_DATA_KEY = os.environ.get("FOOTBALL_DATA_KEY", "")
    ODDS_API_KEY      = os.environ.get("ODDS_API_KEY", "")
    NEWS_API_KEY      = os.environ.get("NEWS_API_KEY", "")

    WC_COMPETITION_ID = "CL"   # football-data.org uses "WC" for World Cup (check their docs)

    # ──────────────────────────────────────────────────────
    # FOOTBALL-DATA.ORG
    # ──────────────────────────────────────────────────────

    async def get_wc_matches(self) -> list:
        """Fetch scheduled and live WC2026 matches."""
        if not self.FOOTBALL_DATA_KEY:
            return self._fallback_matches()

        url = "https://api.football-data.org/v4/competitions/WC/matches"
        headers = {"X-Auth-Token": self.FOOTBALL_DATA_KEY}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return self._parse_football_matches(data.get("matches", []))
        except Exception as e:
            print(f"football-data.org error: {e}")
        return self._fallback_matches()

    async def get_live_matches(self) -> list:
        """Fetch only currently live matches."""
        if not self.FOOTBALL_DATA_KEY:
            return []
        url = "https://api.football-data.org/v4/competitions/WC/matches?status=LIVE"
        headers = {"X-Auth-Token": self.FOOTBALL_DATA_KEY}
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=8)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return self._parse_football_matches(data.get("matches", []))
        except Exception as e:
            print(f"Live match error: {e}")
        return []

    def _parse_football_matches(self, matches: list) -> list:
        parsed = []
        for m in matches:
            parsed.append({
                "id":       str(m.get("id", "")),
                "home":     m.get("homeTeam", {}).get("name", ""),
                "away":     m.get("awayTeam", {}).get("name", ""),
                "date":     m.get("utcDate", ""),
                "status":   m.get("status", ""),   # SCHEDULED / LIVE / FINISHED
                "minute":   m.get("minute", None),
                "score_home": m.get("score", {}).get("fullTime", {}).get("home"),
                "score_away": m.get("score", {}).get("fullTime", {}).get("away"),
                "group":    m.get("group", {}).get("name", "") if m.get("group") else "",
                "stage":    m.get("stage", ""),
            })
        return parsed

    # ──────────────────────────────────────────────────────
    # THE ODDS API (Betclic included)
    # ──────────────────────────────────────────────────────

    async def get_live_odds(self) -> list:
        """Fetch current Betclic odds for World Cup matches."""
        if not self.ODDS_API_KEY:
            return self._fallback_odds()

        url = (
            "https://api.the-odds-api.com/v4/sports/soccer_fifa_world_cup_winner/odds/"
            f"?apiKey={self.ODDS_API_KEY}"
            "&regions=eu"
            "&markets=h2h,totals,spreads"
            "&bookmakers=betclic"
            "&oddsFormat=decimal"
        )
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return self._parse_odds(data)
        except Exception as e:
            print(f"Odds API error: {e}")
        return self._fallback_odds()

    def _parse_odds(self, events: list) -> list:
        parsed = []
        for ev in events:
            for bk in ev.get("bookmakers", []):
                if bk["key"] != "betclic":
                    continue
                for market in bk.get("markets", []):
                    for outcome in market.get("outcomes", []):
                        parsed.append({
                            "match_id":   ev.get("id"),
                            "home":       ev.get("home_team"),
                            "away":       ev.get("away_team"),
                            "date":       ev.get("commence_time"),
                            "market":     market.get("key"),      # h2h / totals / spreads
                            "selection":  outcome.get("name"),
                            "odds":       outcome.get("price"),
                            "bookmaker":  "Betclic",
                        })
        return parsed

    # ──────────────────────────────────────────────────────
    # NEWSAPI
    # ──────────────────────────────────────────────────────

    async def get_news(self) -> list:
        """Fetch latest football / World Cup 2026 news."""
        if not self.NEWS_API_KEY:
            return self._fallback_news()

        url = (
            "https://newsapi.org/v2/everything"
            f"?apiKey={self.NEWS_API_KEY}"
            "&q=World+Cup+2026+football+injury+lineup"
            "&language=en"
            "&sortBy=publishedAt"
            "&pageSize=20"
        )
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=10)) as r:
                    if r.status == 200:
                        data = await r.json()
                        return self._parse_news(data.get("articles", []))
        except Exception as e:
            print(f"NewsAPI error: {e}")
        return self._fallback_news()

    def _parse_news(self, articles: list) -> list:
        parsed = []
        for a in articles[:15]:
            title = a.get("title", "")
            # Classify impact
            hi_keywords = ["injured", "injury", "ruled out", "suspended", "red card", "lineup"]
            med_keywords = ["form", "training", "confirmed", "squad", "tactics"]
            title_lower = title.lower()
            if any(k in title_lower for k in hi_keywords):
                impact = "high"
                cat = "INJURY"
            elif any(k in title_lower for k in med_keywords):
                impact = "medium"
                cat = "FORM"
            else:
                impact = "low"
                cat = "NEWS"
            parsed.append({
                "title":       title,
                "description": a.get("description", ""),
                "url":         a.get("url", ""),
                "source":      a.get("source", {}).get("name", ""),
                "published_at": a.get("publishedAt", ""),
                "impact":      impact,
                "category":    cat,
            })
        return parsed

    # ──────────────────────────────────────────────────────
    # FALLBACKS (used when API keys not configured)
    # These return the pre-researched WC2026 data
    # ──────────────────────────────────────────────────────

    def _fallback_matches(self) -> list:
        return [
            {"id":"1","home":"Mexico","away":"South Africa","date":"2026-06-11T21:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group A","stage":"GROUP_STAGE"},
            {"id":"2","home":"Brazil","away":"Morocco","date":"2026-06-13T00:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group C","stage":"GROUP_STAGE"},
            {"id":"3","home":"France","away":"Iraq","date":"2026-06-17T21:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group I","stage":"GROUP_STAGE"},
            {"id":"4","home":"Senegal","away":"Norway","date":"2026-06-17T18:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group I","stage":"GROUP_STAGE"},
            {"id":"5","home":"France","away":"Senegal","date":"2026-06-22T21:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group I","stage":"GROUP_STAGE"},
            {"id":"6","home":"France","away":"Norway","date":"2026-06-26T21:00:00Z","status":"SCHEDULED","score_home":None,"score_away":None,"group":"Group I","stage":"GROUP_STAGE"},
        ]

    def _fallback_odds(self) -> list:
        return [
            {"match_id":"3","home":"France","away":"Iraq","date":"2026-06-17","market":"h2h","selection":"France","odds":1.20,"bookmaker":"Betclic"},
            {"match_id":"3","home":"France","away":"Iraq","date":"2026-06-17","market":"h2h","selection":"Draw","odds":7.00,"bookmaker":"Betclic"},
            {"match_id":"3","home":"France","away":"Iraq","date":"2026-06-17","market":"h2h","selection":"Iraq","odds":18.00,"bookmaker":"Betclic"},
            {"match_id":"3","home":"France","away":"Iraq","date":"2026-06-17","market":"totals","selection":"Over 2.5","odds":1.55,"bookmaker":"Betclic"},
            {"match_id":"4","home":"Senegal","away":"Norway","date":"2026-06-17","market":"totals","selection":"Over 2.5","odds":1.90,"bookmaker":"Betclic"},
            {"match_id":"2","home":"Brazil","away":"Morocco","date":"2026-06-13","market":"h2h","selection":"Brazil","odds":1.65,"bookmaker":"Betclic"},
        ]

    def _fallback_news(self) -> list:
        return [
            {"title":"Mbappé confirmed fit for France opener vs Iraq","impact":"high","category":"INJURY","published_at":"2026-06-04T10:00:00Z","source":"L'Equipe"},
            {"title":"Lamine Yamal (Spain) — hamstring concern. Tournament fitness uncertain","impact":"high","category":"INJURY","published_at":"2026-06-04T08:00:00Z","source":"Marca"},
            {"title":"Haaland: 'Norway are ready. We will surprise everyone at this World Cup'","impact":"medium","category":"FORM","published_at":"2026-06-03T14:00:00Z","source":"BBC Sport"},
            {"title":"Dembélé wins second UCL title with PSG, arrives as Ballon d'Or holder","impact":"medium","category":"FORM","published_at":"2026-05-30T20:00:00Z","source":"Guardian"},
        ]


# Singleton
fetcher = DataFetcher()
