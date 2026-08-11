"""Fill the listing-detail columns on rows that were stored before they existed.

`4b33658` and `d935211` added `listed_at`, `contact_phone` and `contact_email`,
and `b252736` started comparing asking prices per m2 and per are — which needs
`house_m2` and `plot_ares`. All of it arrives through `adapters.rinka.
parse_detail`, and none of it ever reaches a row that was already in the table:
`poller.poll_source` only looks at ids above its watermark, and `mailbox._insert`
returns early on a matching fingerprint. Re-polling therefore backfills nothing,
by design — this module is the way back.

Run it:

    python -m backend.app.sources.backfill --dry-run
    python -m backend.app.sources.backfill [--limit N]

It is the only maintenance code allowed to open a connection, and it stays
inside the same rules the poller obeys:

* **registry.py decides.** Each row's `source` goes through `assert_pollable`,
  which refuses everything not declared POLL *and* every key the table does not
  mention. The row's own URL is then checked against that source's host, so a
  row that says `rinka` while pointing somewhere else is refused rather than
  fetched — the registry's verdict is about a host, and this is the only place
  where the host arrives as stored data rather than from an adapter.
* **The site's `crawl_delay_s` is honoured** before every fetch, exactly as
  `poller.poll_source` does it, using the same `_http_fetch`.
* **No second HTML parser.** `adapter.parse_detail` does the reading.

WHY ARCHIVED ROWS ARE NEVER TOUCHED. `api.update_candidate` erases
`contact_phone` and `contact_email` in the same statement that sets `archived`:
those are a private individual's details, kept for one purpose — asking about
this property — and archiving is the operator saying that purpose is spent. A
backfill that refetched an archived row would put the phone number straight
back and silently undo a deliberate erasure. Filling only `listed_at` on those
rows and dropping the contacts would still mean fetching the advert of a
rejected property to gather data about it, for a "days on the market" figure
nobody will read on a row that is out of every default view. So archived rows
are skipped whole, they are counted so the skip is visible rather than
invisible, and `archived=0` is re-checked in the UPDATE's WHERE clause — the
operator may archive a row through the running app while this is mid-flight.

IDEMPOTENCE. The work list is `listed_at IS NULL`, so a row that has been done
is never selected again and a second run costs nothing. `listed_at` is the only
usable marker: an absent phone or an absent floor area is the normal, correct
outcome for many adverts (no email appeared on any of the 23 live listings
measured), so keying on those would refetch the same pages forever. A row is
written only when a date parsed — a page that 404s, redirects or carries no
readable date leaves the row exactly as it was, is counted as failed, and is
retried on the next run. Nothing half-parsed is ever written.

Profiles are NOT re-evaluated here even though this fills fields they test.
`POST /api/candidates/reevaluate` exists for that and says so; running it after
a large backfill is the right sequence.
"""
from __future__ import annotations
import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from ..db import connect, init_db, log_refresh
from . import adapters
from . import registry as reg
from .poller import Fetch, _http_fetch

log = logging.getLogger(__name__)

# Every row that has a link and no listing date, oldest first. The columns
# fetched are the ones the UPDATE needs to know the previous value of, so the
# report can say which fields actually changed.
_SELECT_SQL = (
    "SELECT id, ref, source, url, archived, listed_at, contact_phone, "
    "contact_email, house_m2, plot_ares "
    "FROM candidate WHERE listed_at IS NULL AND url IS NOT NULL AND url <> '' "
    "ORDER BY id"
)

# Only these are ever written, and only where the stored value is NULL
# (COALESCE below). A backfill fills blanks; it does not restate a price or
# overwrite a floor area the operator corrected by hand after reading the
# advert. price_eur is deliberately not among them: it is mirrored into
# costs_json["purchase"] at ingest, and moving one without the other would
# quietly change what the scorer costs the project at.
_FILLED = ("listed_at", "contact_phone", "contact_email", "house_m2", "plot_ares")


def _rows_needing_details() -> list[dict[str, Any]]:
    with connect() as cx:
        return [dict(r) for r in cx.execute(_SELECT_SQL).fetchall()]


def _permitted(row: dict[str, Any]) -> tuple[reg.Source | None, str]:
    """(source, "") if this row may be fetched, (None, reason) if it may not.

    Refuses before anything is opened, in registry.py's own words where it has
    them. The host comparison is this module's own addition: `assert_pollable`
    answers for a source key, and the URL here is stored data that nothing has
    re-validated since it was written.
    """
    try:
        source = reg.assert_pollable(row["source"])
    except reg.PolicyError as exc:
        return None, str(exc)
    if adapters.get(row["source"]) is None:
        return None, f"„{row['source']}“ leidžiamas, bet adapteris neparašytas"
    parts = urlsplit(row["url"] or "")
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or host.lower() != source.host.lower():
        return None, (f"nuoroda veda ne į {source.host} — nesiunčiama "
                      f"({row['url'][:80]})")
    return source, ""


def _write(row: dict[str, Any], detail: dict[str, Any]) -> int:
    """Fill the blank columns of one row. Returns the number of rows written.

    `archived=0 AND listed_at IS NULL` is repeated in the WHERE clause on
    purpose. The app keeps serving while this runs, so the operator can archive
    the very row being fetched — and archiving erases the contacts this
    statement would otherwise put back. Zero rows written means the row moved
    under us and the run reports it rather than pretending it succeeded.
    """
    with connect() as cx:
        cur = cx.execute(
            "UPDATE candidate SET listed_at=?, "
            "contact_phone=COALESCE(contact_phone,?), "
            "contact_email=COALESCE(contact_email,?), "
            "house_m2=COALESCE(house_m2,?), plot_ares=COALESCE(plot_ares,?), "
            "updated_at=datetime('now') "
            "WHERE id=? AND archived=0 AND listed_at IS NULL",
            (detail.get("listed_at"), detail.get("contact_phone"),
             detail.get("contact_email"), detail.get("house_m2"),
             detail.get("plot_ares"), row["id"]))
        return cur.rowcount


def _changed(row: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    """The fields the UPDATE actually filled, for the report."""
    return {k: detail.get(k) for k in _FILLED
            if row.get(k) is None and detail.get(k) is not None}


async def backfill_details(fetch: Fetch | None = None, limit: int | None = None,
                           dry_run: bool = False) -> dict[str, Any]:
    """One pass. Safe to interrupt and safe to repeat; see the module docstring.

    `limit` caps the number of pages fetched, not the number of rows looked at,
    because the fetches are what cost time and the site's goodwill. `dry_run`
    performs no request and writes nothing at all — not even the refresh log.
    """
    fetch = fetch or _http_fetch
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = _rows_needing_details()

    examined = fetched = updated = skipped_policy = skipped_archived = failed = 0
    planned: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for row in rows:
        if limit is not None and (fetched + len(planned)) >= limit:
            break
        examined += 1

        if row["archived"]:
            skipped_archived += 1
            report.append({"ref": row["ref"], "outcome": "archived",
                           "detail": "archyvuotas — kontaktai ištrinti sąmoningai"})
            continue

        source, refusal = _permitted(row)
        if source is None:
            skipped_policy += 1
            report.append({"ref": row["ref"], "outcome": "policy", "detail": refusal})
            log.warning("%s: %s", row["ref"], refusal)
            continue

        if dry_run:
            planned.append({"ref": row["ref"], "url": row["url"]})
            report.append({"ref": row["ref"], "outcome": "would_fetch",
                           "detail": row["url"]})
            continue

        await asyncio.sleep(source.crawl_delay_s)
        try:
            status, html = await fetch(row["url"])
        except Exception as exc:                      # network, DNS, timeout
            fetched += 1
            failed += 1
            report.append({"ref": row["ref"], "outcome": "failed",
                           "detail": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        fetched += 1

        if status != 200:
            failed += 1
            report.append({"ref": row["ref"], "outcome": "failed",
                           "detail": f"HTTP {status}"})
            continue

        try:
            detail = adapters.get(row["source"]).parse_detail(html, row["url"])
        except Exception as exc:
            log.exception("%s: parse_detail raised", row["ref"])
            failed += 1
            report.append({"ref": row["ref"], "outcome": "failed",
                           "detail": f"{type(exc).__name__}: {exc}"[:200]})
            continue

        if not detail.get("listed_at"):
            # No date means either an unparseable page or not the listing at
            # all (redirect, teaser, "advert removed"). Either way the row is
            # left untouched rather than half-filled from a page we could not
            # read, and it stays on the work list for the next run.
            failed += 1
            report.append({"ref": row["ref"], "outcome": "failed",
                           "detail": "datos nepavyko perskaityti"})
            continue

        changed = _changed(row, detail)
        if _write(row, detail):
            updated += 1
            report.append({"ref": row["ref"], "outcome": "updated", "changed": changed})
        else:
            # Archived or filled by something else between the SELECT and here.
            skipped_archived += 1
            report.append({"ref": row["ref"], "outcome": "archived",
                           "detail": "pasikeitė vykdymo metu — neliesta"})

    summary = {
        "dry_run": dry_run,
        "examined": examined,
        "fetched": fetched,
        "updated": updated,
        "skipped_policy": skipped_policy,
        "skipped_archived": skipped_archived,
        "failed": failed,
        "pending": max(len(rows) - examined, 0),
        "rows": report,
    }
    detail_line = (f"peržiūrėta {examined}; parsiųsta {fetched}; papildyta {updated}; "
                   f"praleista pagal taisykles {skipped_policy}; "
                   f"archyvuotų nepaliesta {skipped_archived}; nepavyko {failed}")
    if not dry_run:
        log_refresh("backfill", "ok", detail_line, updated, started)
    log.info("backfill: %s%s", "(bandomasis) " if dry_run else "", detail_line)
    return summary


# --------------------------------------------------------------------- CLI
def _utf8_stdout() -> None:
    """Do not let a Lithuanian summary kill the run on Windows.

    The report is Lithuanian, like every other operator-facing string here. On
    Windows, stdout attached to a console handles that fine, but stdout piped
    or redirected to a file falls back to the locale encoding (cp1252), where
    the first `ū` raises UnicodeEncodeError — after the work is done and
    committed, so the operator sees a traceback instead of the summary of a run
    that actually succeeded. `backslashreplace` keeps the character visible
    even on a terminal that cannot draw it.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    except (AttributeError, ValueError):        # not a reconfigurable stream
        pass


def _print(summary: dict[str, Any]) -> None:
    for r in summary["rows"]:
        if r["outcome"] == "updated":
            bits = ", ".join(f"{k}={v}" for k, v in (r.get("changed") or {}).items())
            print(f"  {r['ref']:<6} papildyta   {bits or '—'}")
        else:
            label = {"would_fetch": "būtų siųsta", "policy": "neleidžiama",
                     "archived": "archyvuotas", "failed": "nepavyko"}[r["outcome"]]
            print(f"  {r['ref']:<6} {label:<11} {r.get('detail', '')}")
    print(f"\nperžiūrėta {summary['examined']}; parsiųsta {summary['fetched']}; "
          f"papildyta {summary['updated']}; praleista pagal taisykles "
          f"{summary['skipped_policy']}; archyvuotų nepaliesta "
          f"{summary['skipped_archived']}; nepavyko {summary['failed']}"
          + (f"; liko {summary['pending']}" if summary["pending"] else ""))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m backend.app.sources.backfill",
        description="Papildo jau išsaugotus skelbimus data ir kontaktais "
                    "(listed_at, contact_phone, contact_email, house_m2, "
                    "plot_ares). Kartojama saugiai.")
    ap.add_argument("--dry-run", action="store_true",
                    help="tik parodo, ką siųstųsi; nieko nekeičia ir nieko nesiunčia")
    ap.add_argument("--limit", type=int, default=None,
                    help="daugiausiai tiek puslapių per vieną paleidimą")
    args = ap.parse_args(argv)

    _utf8_stdout()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    init_db()          # additive migrations, in case the database predates the columns
    _print(asyncio.run(backfill_details(limit=args.limit, dry_run=args.dry_run)))
    return 0


if __name__ == "__main__":      # pragma: no cover - exercised by hand, not by pytest
    raise SystemExit(main())
