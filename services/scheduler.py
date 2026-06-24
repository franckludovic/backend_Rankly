"""
services/scheduler.py
=====================
APScheduler setup for scheduled re-audits.
Runs every 6 hours, checks the `scheduled_audits` Supabase table for due jobs,
and re-runs `run_full_analysis()` for each. Requires:
    pip install apscheduler==3.11.*
"""

import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

logger    = logging.getLogger(__name__)
_sched    = AsyncIOScheduler()


async def _run_due_audits() -> None:
    from storage import schedule_repository, audit_repository
    from services.audit_engine import run_full_analysis

    due = schedule_repository.get_due_schedules()
    if not due:
        return

    logger.info(f"Scheduler: {len(due)} audit(s) due")
    for job in due:
        try:
            logger.info(f"  Re-auditing {job['url']} / {job['keyword']}")
            data = await run_full_analysis(job["url"], job["keyword"])
            audit_repository.create_audit(job["user_id"], job["url"], job["keyword"], data)
            schedule_repository.update_next_run(job["id"], job["frequency"])
            logger.info(f"  Done- {job['url']}")
        except Exception as e:
            logger.error(f"  Failed for {job['url']}: {e}")


async def _run_competitor_checks() -> None:
    from services.competitor_monitor import check_all_watches
    logger.info("Scheduler: running competitor monitor check")
    try:
        await check_all_watches()
    except Exception as e:
        logger.error(f"Competitor monitor check failed: {e}")


def start_scheduler() -> None:
    if _sched.running:
        return
    _sched.add_job(
        _run_due_audits,
        IntervalTrigger(hours=6),
        id="scheduled_audits",
        replace_existing=True,
    )
    _sched.add_job(
        _run_competitor_checks,
        IntervalTrigger(weeks=1),
        id="competitor_monitor",
        replace_existing=True,
    )
    _sched.start()
    logger.info("APScheduler started- audits every 6 h · competitor checks weekly")


def stop_scheduler() -> None:
    if _sched.running:
        _sched.shutdown(wait=False)
        logger.info("APScheduler stopped")
