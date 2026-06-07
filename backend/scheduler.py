"""
WC2026 Autonomous Scheduler
────────────────────────────
Runs background jobs automatically — no user action needed.

Schedule:
  Every 15s   → refresh live match scores (only during matches)
  Every 60s   → refresh Betclic odds
  Every 5min  → refresh news feed
  Every 08:00 → run full agent analysis cycle (Betting + Audit)
  Every 22:00 → generate WhatsApp daily brief
  On demand   → triggered by manual /api/run-agent call
"""

import asyncio
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

scheduler = AsyncIOScheduler()


def start_scheduler():
    """Register all jobs and start the scheduler."""

    # Lazy import to avoid circular imports
    from main import (
        refresh_live,
        refresh_odds,
        refresh_news,
        run_analysis_cycle,
    )

    # ── Live match data (every 15 seconds) ──────────────────────
    scheduler.add_job(
        _run_async(refresh_live),
        trigger=IntervalTrigger(seconds=15),
        id="live_scores",
        name="Live Score Refresh",
        max_instances=1,
        misfire_grace_time=10,
    )

    # ── Odds refresh (every 60 seconds) ─────────────────────────
    scheduler.add_job(
        _run_async(refresh_odds),
        trigger=IntervalTrigger(seconds=60),
        id="odds_refresh",
        name="Odds Refresh",
        max_instances=1,
        misfire_grace_time=30,
    )

    # ── News refresh (every 5 minutes) ──────────────────────────
    scheduler.add_job(
        _run_async(refresh_news),
        trigger=IntervalTrigger(minutes=5),
        id="news_refresh",
        name="News Refresh",
        max_instances=1,
        misfire_grace_time=60,
    )

    # ── Morning analysis cycle (08:00 UTC daily) ────────────────
    scheduler.add_job(
        _run_async(run_analysis_cycle),
        trigger=CronTrigger(hour=8, minute=0, timezone="UTC"),
        id="morning_analysis",
        name="Morning Agent Analysis",
        max_instances=1,
    )

    # ── Pre-match analysis (60 minutes before each match) ───────
    # This is handled dynamically — the morning cycle detects matches
    # within the next 90 minutes and schedules a pre-match run

    # ── Second daily cycle (16:00 UTC — afternoon matches) ──────
    scheduler.add_job(
        _run_async(run_analysis_cycle),
        trigger=CronTrigger(hour=16, minute=0, timezone="UTC"),
        id="afternoon_analysis",
        name="Afternoon Agent Analysis",
        max_instances=1,
    )

    scheduler.start()
    print("✅ Scheduler started. Jobs:")
    for job in scheduler.get_jobs():
        print(f"   [{job.id}] {job.name} — next: {job.next_run_time}")


def _run_async(coro_fn):
    """Wrap an async function for APScheduler (which expects sync callables)."""
    def wrapper():
        loop = asyncio.get_event_loop()
        if loop.is_running():
            asyncio.create_task(coro_fn())
        else:
            loop.run_until_complete(coro_fn())
    return wrapper
