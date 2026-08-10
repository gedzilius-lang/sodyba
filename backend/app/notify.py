"""Push notification for listings that clear a filter profile.

Telegram only — you already run bots there, and it needs no inbound port.
Silently disabled when the token is unset.
"""
from __future__ import annotations
import logging
from typing import Any

import httpx

from .config import TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, HTTP_TIMEOUT

log = logging.getLogger(__name__)


def enabled() -> bool:
    return bool(TELEGRAM_TOKEN and TELEGRAM_CHAT_ID)


def _esc(s: Any) -> str:
    return (str(s or "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def _line(c: dict[str, Any]) -> str:
    price = f"{c['price_eur']:,.0f} EUR".replace(",", " ") if c.get("price_eur") else "kaina ?"
    bits = [b for b in (
        f"{c['house_m2']:.0f} m²" if c.get("house_m2") else None,
        f"{c['plot_ares']:.0f} a" if c.get("plot_ares") else None,
    ) if b]
    head = f"<b>{_esc(c['ref'])}</b> · {_esc(c.get('locality') or c.get('title') or '—')}"
    meta = " · ".join([_esc(c.get("municipality") or "?"), price] + bits)
    tags = ", ".join(_esc(p) for p in c.get("profiles", []))
    url = f"\n{_esc(c['url'])}" if c.get("url") else ""
    return f"{head}\n{meta}\n<i>{tags}</i> · {_esc(c.get('source'))}{url}"


async def push(created: list[dict[str, Any]]) -> None:
    if not created or not enabled():
        return
    head = f"🏚 {len(created)} nauj{'as objektas' if len(created) == 1 else 'i objektai'}"
    body = "\n\n".join(_line(c) for c in created[:10])
    if len(created) > 10:
        body += f"\n\n… ir dar {len(created) - 10}."
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            await client.post(
                f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
                json={"chat_id": TELEGRAM_CHAT_ID, "text": f"{head}\n\n{body}",
                      "parse_mode": "HTML", "disable_web_page_preview": False},
            )
    except Exception:
        log.exception("telegram push failed")
