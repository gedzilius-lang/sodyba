"""Collector for the Lithuanian Real Property Register (NTR) open data.

Source: VĮ Registrų centras via get.data.gov.lt (Spinta API).
robots.txt on that host is `Allow: /`, so automated polling is permitted. We
still rate-limit and identify ourselves via User-Agent.

What this gives us: the *stock* of single-family residential buildings per
municipality, split by registered electricity, plumbing, age and wall material.
It is market context, not listings — RC publishes no transaction prices and the
bailiff auction datasets are aggregated with prices stripped.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone

import httpx

from ..config import (DATA_GOV_BASE, HTTP_TIMEOUT, HTTP_UA, REQUEST_DELAY,
                      PASK_TIPAS_VIENBUTIS)
from ..db import connect, log_refresh

log = logging.getLogger(__name__)

MUNI_MODEL = "datasets/gov/rc/ar/savivaldybe/Savivaldybe"
BLDG_MODEL = "datasets/gov/rc/ntr/ntr_pastatai/NtrPastatas"
LOG_WALL_CODE = 30  # `sienos` classifier value for log construction


async def _get(client: httpx.AsyncClient, model: str, query: str = "") -> dict:
    url = DATA_GOV_BASE + model + (f"?{query}" if query else "")
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json()
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:  # network flake, upstream 500
            last = exc
        await asyncio.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{model}?{query} failed: {last}")


async def _count(client: httpx.AsyncClient, base_filter: str, extra: str = "") -> int:
    data = await _get(client, BLDG_MODEL, f"{base_filter}{extra}&count()")
    rows = data.get("_data") or []
    await asyncio.sleep(REQUEST_DELAY)
    return int(rows[0]["count()"]) if rows else 0


async def refresh_market_stock(municipalities: list[str]) -> int:
    """Repopulate market_stock for the named municipalities. Returns row count."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    written = 0
    skipped: list[str] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, headers={"User-Agent": HTTP_UA,
                                                                "Accept": "application/json"}) as client:
        try:
            lookup = {
                r["pavadinimas"]: r["_id"]
                for r in (await _get(client, MUNI_MODEL, "limit(100)"))["_data"]
            }
        except Exception as exc:
            log_refresh("ntr", "error", f"municipality lookup failed: {exc}", 0, started)
            raise

        for name in municipalities:
            sid = lookup.get(name)
            if not sid:
                log.warning("unknown municipality %r — skipped", name)
                skipped.append(f"{name} (nežinoma)")
                continue
            base = f'pask_tipas.pask_tipas={PASK_TIPAS_VIENBUTIS}&savivaldybe._id="{sid}"'
            try:
                row = (
                    await _count(client, base),
                    await _count(client, base, "&elektra=1"),
                    await _count(client, base, "&vandentiekis=1"),
                    await _count(client, base, "&elektra=1&vandentiekis=1"),
                    await _count(client, base, "&stat_pabaigos_metai<1945"),
                    await _count(client, base, f"&sienos={LOG_WALL_CODE}"),
                )
            except Exception as exc:
                # Upstream returns sporadic 500s on some filter combinations.
                # Skip and keep going; the next run picks it up.
                log.error("municipality %s failed: %s", name, exc)
                skipped.append(name)
                continue

            with connect() as cx:
                cx.execute(
                    "INSERT INTO market_stock(municipality,total,with_power,with_water,"
                    "power_and_water,pre_1945,log_walls,fetched_at) "
                    "VALUES(?,?,?,?,?,?,?,datetime('now')) "
                    "ON CONFLICT(municipality) DO UPDATE SET total=excluded.total,"
                    "with_power=excluded.with_power,with_water=excluded.with_water,"
                    "power_and_water=excluded.power_and_water,pre_1945=excluded.pre_1945,"
                    "log_walls=excluded.log_walls,fetched_at=datetime('now')",
                    (name, *row),
                )
            written += 1
            log.info("ntr %s -> total=%s power+water=%s", name, row[0], row[3])

    status = "ok" if not skipped else "partial"
    detail = f"{written} atnaujinta"
    if skipped:
        detail += f"; praleista: {', '.join(skipped)}"
    log_refresh("ntr", status, detail, written, started)
    return written
