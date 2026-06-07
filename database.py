"""
WC2026 Database Layer — Supabase (PostgreSQL)
──────────────────────────────────────────────
Falls back to in-memory + JSON file if Supabase not configured.
This means you can run the agent locally without any database setup.
"""

import os, json, uuid
from datetime import datetime, timezone
from pathlib import Path

try:
    from supabase import create_client, Client
    SUPABASE_AVAILABLE = True
except ImportError:
    SUPABASE_AVAILABLE = False


class Database:

    def __init__(self):
        self.supabase: "Client | None" = None
        self._local_path = Path(__file__).parent / "local_data.json"
        self._local: dict = self._load_local()
        # Ensure base structure
        for key in ("bets", "recommendations", "learning_log", "cache", "config"):
            self._local.setdefault(key, {} if key in ("cache", "config") else [])

    # ─────────────────────────────────────────────────────
    # INIT (call once at startup)
    # ─────────────────────────────────────────────────────

    async def init(self):
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_KEY")
        if SUPABASE_AVAILABLE and url and key:
            self.supabase = create_client(url, key)
            print("✅ Connected to Supabase")
            await self._ensure_tables()
        else:
            print("⚠️  No Supabase configured — using local JSON storage")

    async def _ensure_tables(self):
        """Supabase tables are created via the dashboard — this is a no-op here."""
        pass

    # ─────────────────────────────────────────────────────
    # LOCAL FALLBACK
    # ─────────────────────────────────────────────────────

    def _load_local(self) -> dict:
        if self._local_path.exists():
            try:
                return json.loads(self._local_path.read_text())
            except Exception:
                pass
        return {}

    def _save_local(self):
        self._local_path.write_text(json.dumps(self._local, indent=2, default=str))

    # ─────────────────────────────────────────────────────
    # BETS
    # ─────────────────────────────────────────────────────

    async def get_bets(self) -> list:
        if self.supabase:
            res = self.supabase.table("bets").select("*").order("created_at", desc=True).execute()
            return res.data
        return list(reversed(self._local["bets"]))

    async def create_bet(self, data: dict) -> dict:
        bet = {
            "id":            str(uuid.uuid4()),
            "match":         data.get("match", ""),
            "date":          data.get("date", ""),
            "market":        data.get("market", ""),
            "selection":     data.get("selection", ""),
            "rec_odds":      data.get("rec_odds", data.get("odds")),
            "actual_odds":   data.get("actual_odds", data.get("odds")),
            "rec_stake":     data.get("rec_stake", data.get("stake")),
            "actual_stake":  data.get("actual_stake", data.get("stake")),
            "confidence":    data.get("confidence", 7.0),
            "risk":          data.get("risk", "Moderate"),
            "reasoning":     data.get("reasoning", ""),
            "status":        "pending",
            "closing_odds":  None,
            "is_accumulator": data.get("is_accumulator", False),
            "rec_id":        data.get("rec_id"),
            "created_at":    datetime.now(timezone.utc).isoformat(),
        }
        if self.supabase:
            res = self.supabase.table("bets").insert(bet).execute()
            return res.data[0]
        self._local["bets"].append(bet)
        self._save_local()
        return bet

    async def update_bet(self, bet_id: str, updates: dict) -> dict | None:
        if self.supabase:
            res = self.supabase.table("bets").update(updates).eq("id", bet_id).execute()
            return res.data[0] if res.data else None
        for i, b in enumerate(self._local["bets"]):
            if b["id"] == bet_id:
                self._local["bets"][i].update(updates)
                self._save_local()
                return self._local["bets"][i]
        return None

    async def delete_bet(self, bet_id: str):
        if self.supabase:
            self.supabase.table("bets").delete().eq("id", bet_id).execute()
            return
        self._local["bets"] = [b for b in self._local["bets"] if b["id"] != bet_id]
        self._save_local()

    # ─────────────────────────────────────────────────────
    # RECOMMENDATIONS
    # ─────────────────────────────────────────────────────

    async def get_recommendations(self) -> list:
        if self.supabase:
            res = self.supabase.table("recommendations").select("*").order("created_at", desc=True).limit(50).execute()
            return res.data
        return list(reversed(self._local["recommendations"]))

    async def upsert_recommendation(self, rec: dict):
        rec.setdefault("id", str(uuid.uuid4()))
        rec["created_at"] = datetime.now(timezone.utc).isoformat()
        if self.supabase:
            self.supabase.table("recommendations").upsert(rec).execute()
            return
        # Overwrite by match+selection
        self._local["recommendations"] = [
            r for r in self._local["recommendations"]
            if not (r.get("match") == rec.get("match") and r.get("selection") == rec.get("selection"))
        ]
        self._local["recommendations"].append(rec)
        self._save_local()

    # ─────────────────────────────────────────────────────
    # LEARNING LOG
    # ─────────────────────────────────────────────────────

    async def save_learning_log(self, entry: dict):
        entry.setdefault("id", str(uuid.uuid4()))
        entry.setdefault("created_at", datetime.now(timezone.utc).isoformat())
        if self.supabase:
            self.supabase.table("learning_log").insert(entry).execute()
            return
        self._local["learning_log"].append(entry)
        self._save_local()

    async def get_learning_log(self) -> list:
        if self.supabase:
            res = self.supabase.table("learning_log").select("*").order("created_at", desc=True).limit(20).execute()
            return res.data
        return list(reversed(self._local["learning_log"]))

    # ─────────────────────────────────────────────────────
    # CONFIG (bankroll, agent name, etc.)
    # ─────────────────────────────────────────────────────

    async def get_config(self) -> dict:
        if self.supabase:
            res = self.supabase.table("config").select("*").execute()
            return {r["key"]: r["value"] for r in (res.data or [])}
        return self._local.get("config", {})

    async def set_config(self, key: str, value):
        if self.supabase:
            self.supabase.table("config").upsert({"key": key, "value": value}).execute()
            return
        self._local.setdefault("config", {})[key] = value
        self._save_local()

    # ─────────────────────────────────────────────────────
    # CACHE (live data — not persisted)
    # ─────────────────────────────────────────────────────

    _cache: dict = {}

    async def get_cached(self, key: str):
        return self._cache.get(key)

    async def set_cache(self, key: str, value):
        self._cache[key] = value


# Singleton
db = Database()
