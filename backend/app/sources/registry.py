"""Which sources may be fetched, and on whose authority. PURE.

Every verdict below was read from the source's own robots.txt on the date in
`checked_at`. This is not documentation — `poller.py` calls `assert_pollable`
before it opens a connection, so a source absent from this table, or present
with any policy but POLL, cannot be fetched at all.

robots.txt changes. alio.lt added its AI-crawler blocks in July 2026, one month
before this table was written. `stale()` exists so an unchecked verdict becomes
a warning instead of an assumption.
"""
from __future__ import annotations
from dataclasses import dataclass
from datetime import date

POLL = "poll"
ALERT_ONLY = "alert_only"
LINK_ONLY = "link_only"
MANUAL = "manual"


class PolicyError(Exception):
    """Raised when something tries to fetch a source it may not fetch."""


@dataclass(frozen=True)
class Source:
    key: str
    host: str
    policy: str
    robots: str
    checked_at: str          # ISO date, YYYY-MM-DD
    crawl_delay_s: float = 1.0


SOURCES: list[Source] = [
    Source("data_gov", "get.data.gov.lt", POLL,
           "Allow: / — official open-data API", "2026-08-10", 1.0),
    Source("rinka", "www.rinka.lt", POLL,
           "User-agent: * / Disallow: (empty) — fully open", "2026-08-10", 2.0),
    Source("zudc", "zudc.lt", POLL,
           "Disallow: (empty) — state land auctions", "2026-08-10", 2.0),
    Source("ntaukcionai", "www.ntaukcionai.lt", POLL,
           "Allow with Crawl-delay: 10", "2026-08-10", 10.0),
    Source("adminbiuras", "www.adminbiuras.lt", POLL,
           "Allow: / — bankruptcy estates", "2026-08-10", 2.0),
    Source("turtas", "turtas.lt", POLL,
           "Disallow: (empty)", "2026-08-10", 2.0),

    Source("aukcionai_turtas", "aukcionai.turtas.lt", LINK_ONLY,
           "no robots.txt, but the bundle ships a reCAPTCHA site key", "2026-08-10"),

    Source("evarzytynes", "www.evarzytynes.lt", ALERT_ONLY,
           "Disallow: /", "2026-08-10"),
    Source("aruodas", "www.aruodas.lt", ALERT_ONLY,
           "bot-challenge page even for /robots.txt", "2026-08-10"),
    Source("domoplius", "www.domoplius.lt", ALERT_ONLY,
           "bot-challenge page even for /robots.txt", "2026-08-10"),
    Source("kampas", "www.kampas.lt", ALERT_ONLY,
           "/robots.txt returns HTTP 403", "2026-08-10"),
    Source("skelbiu", "www.skelbiu.lt", ALERT_ONLY,
           "Allow: / but Disallow: /select/ and search params; "
           "blocks anthropic-ai and Claude-Web by name", "2026-08-10"),
    Source("alio", "www.alio.lt", ALERT_ONLY,
           "Disallow: /public/textSearch/*, /public/category/search*; "
           "GPTBot, ClaudeBot, Amazonbot blocked July 2026", "2026-08-10"),

    Source("facebook", "www.facebook.com", MANUAL,
           "ToS forbids automated collection — paste route only", "2026-08-10"),
    Source("manual", "", MANUAL, "pasted by hand", "2026-08-10"),
]

_BY_KEY = {s.key: s for s in SOURCES}


def get(key: str) -> Source | None:
    return _BY_KEY.get(key)


def is_pollable(key: str) -> bool:
    s = _BY_KEY.get(key)
    return bool(s and s.policy == POLL)


def assert_pollable(key: str) -> Source:
    """Gate every outbound fetch. Refuses unknown sources, not just forbidden ones."""
    s = _BY_KEY.get(key)
    if s is None:
        raise PolicyError(
            f"nežinomas šaltinis „{key}“ — įrašyk jį į registry.SOURCES ir "
            f"pirma patikrink jo robots.txt")
    if s.policy != POLL:
        raise PolicyError(
            f"„{key}“ ({s.host}) pažymėtas kaip {s.policy}: {s.robots}. "
            f"Automatinis skaitymas draudžiamas.")
    return s


def stale(today: str, max_age_days: int = 90) -> list[str]:
    """Keys whose robots.txt verdict is older than max_age_days."""
    now = date.fromisoformat(today)
    out = []
    for s in SOURCES:
        if not s.checked_at:
            continue
        if (now - date.fromisoformat(s.checked_at)).days > max_age_days:
            out.append(s.key)
    return out
