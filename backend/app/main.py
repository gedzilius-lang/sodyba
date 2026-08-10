"""Entrypoint. Serves the API and the static dashboard from one process."""
from __future__ import annotations
import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import date

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from .api import router, WATCHLIST_KEY
from .config import (DEFAULT_MUNICIPALITIES, FRONTEND_DIR, MAILBOX_POLL_MINUTES,
                     POLL_MINUTES, REFRESH_CRON_HOUR, REFRESH_ON_BOOT)
from .db import get_setting, init_db
from .sources import (refresh_market_stock, poll_mailbox, mailbox_configured,
                      refresh_water, refresh_places, refresh_protected,
                      poll_all, registry)
from . import notify

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
log = logging.getLogger("sodyba-radar")


async def scheduled_refresh() -> None:
    munis = get_setting(WATCHLIST_KEY) or DEFAULT_MUNICIPALITIES
    try:
        n = await refresh_market_stock(munis)
        log.info("scheduled refresh complete: %s municipalities", n)
    except Exception:
        log.exception("scheduled refresh failed")


async def ensure_layers() -> None:
    """Nature layers are static reference data — fetch once, then leave alone."""
    from .db import connect
    with connect() as cx:
        have = cx.execute("SELECT COUNT(*) n FROM water_feature").fetchone()["n"]
    if have:
        return
    log.info("nature layers empty — downloading (one-off, a few minutes)")
    try:
        w = await refresh_water()
        p = await refresh_places()
        a = await refresh_protected()
        log.info("layers ready: %s water, %s places, %s protected areas", w, p, a)
    except Exception:
        log.exception("layer download failed — retry via POST /api/layers/refresh")


async def scheduled_mailbox() -> None:
    """Pull new alert emails, score them, push anything that matched."""
    try:
        result = await poll_mailbox()
        if result.get("created"):
            await notify.push(result["created"])
            log.info("mailbox: %s new candidates", len(result["created"]))
    except Exception:
        log.exception("mailbox poll failed")


async def scheduled_poll() -> None:
    """Poll the sources whose robots.txt permits it."""
    try:
        result = await poll_all()
        created = [c for r in result.values() for c in (r.get("created") or [])]
        if created:
            await notify.push(created)
            log.info("poller: %s new candidates", len(created))
    except Exception:
        log.exception("source poll failed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    sched = AsyncIOScheduler(timezone="Europe/Vilnius")
    sched.add_job(scheduled_refresh, CronTrigger(hour=REFRESH_CRON_HOUR, minute=0),
                  id="ntr_refresh", max_instances=1, coalesce=True)
    if mailbox_configured():
        sched.add_job(scheduled_mailbox,
                      IntervalTrigger(minutes=MAILBOX_POLL_MINUTES),
                      id="mailbox_poll", max_instances=1, coalesce=True)
        log.info("mailbox polling every %s min; telegram=%s",
                 MAILBOX_POLL_MINUTES, notify.enabled())
    else:
        log.warning("IMAP not configured — automatic listing ingestion is OFF. "
                    "Set SR_IMAP_* to enable it.")

    sched.add_job(scheduled_poll, IntervalTrigger(minutes=POLL_MINUTES),
                  id="source_poll", max_instances=1, coalesce=True)
    log.info("source polling every %s min: %s", POLL_MINUTES,
             ", ".join(s.key for s in registry.SOURCES
                       if s.policy == registry.POLL))

    stale = registry.stale(date.today().isoformat())
    if stale:
        log.warning("robots.txt verdicts older than 90 days: %s — recheck before "
                    "trusting the poller", ", ".join(stale))

    sched.start()
    log.info("scheduler started; NTR refresh daily at %02d:00 Europe/Vilnius",
             REFRESH_CRON_HOUR)
    asyncio.create_task(ensure_layers())
    if REFRESH_ON_BOOT:
        asyncio.create_task(scheduled_refresh())
    if mailbox_configured():
        asyncio.create_task(scheduled_mailbox())
    try:
        yield
    finally:
        sched.shutdown(wait=False)


app = FastAPI(title="Sodyba Radar", version="1.0", lifespan=lifespan)
app.include_router(router)
app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="frontend")
