# Intake widening — design

Date: 2026-08-12
Status: designed, ready to plan
Supersedes nothing. Prerequisite for: `sklypai` ingestion and property-type
scoring (v2 §5), both of which stay deferred behind Plan B.

---

## 1. What this is

rinka.lt is the only source that both permits polling and lists rural property
for sale. Today the poller reads one of its categories. This adds a second and
makes multi-category ingestion correct, which nothing in the current code is.

**It is a small job, and it should be understood as a small job.** The strategic
finding that produced it is in §2, and it argues against doing more here.

## 2. The measurement that scoped this

Taken from rinka.lt on 2026-08-12:

| category | listings | in the 3 000–25 000 band |
|---|---|---|
| `parduodamos-sodybos` | 86 | polled today, 23 stored |
| `parduodami-namai` | 352 | **~19** (11 of 203 sampled on page 1) |
| `parduodami-sklypai` | 372 | not measured; bare land, different purchase |

The houses category has a median asking price of **185 000 EUR**. Widening to it
adds roughly **19** in-band listings, not 352 — about **+22%**, taking the
national open-market pool this tool can see from 86 to ~105.

Two conclusions follow, and both are load-bearing:

1. **Widening is worth doing but is not the answer to "it doesn't find enough."**
   Lithuanian sellers routinely list a rural homestead as *gyvenamasis namas*
   rather than *sodyba*, so those ~19 are genuinely the same product under a
   different label — a real gain, cheaply had.
2. **The market is thin, and that is not an engineering problem.** About one
   hundred properties nationwide under 25k. The leverage is no longer finding
   more listings; it is determining which of the hundred can actually be bought.
   That is Plan B, and it is the next spec, not this one.

Supporting evidence for (2), already in the database: 24 of 28 candidates have
been advertised for over two years; `K001` for 5.4 years at 18 000 EUR, `K004`
for 5.1. A property sitting five years at 0,05× the peer median has a defect that
kills deals — no legal access, fractional ownership among heirs, dead drainage,
an unregistered building, or a construction ban. `scoring.py` already names five
of those as hard flags and cannot detect any of them from advertisement prose.

## 3. Scope

**In:** `parduodami-namai` alongside `parduodamos-sodybos`; per-category
cursors; pagination; cross-category dedupe.

**Out, deliberately:**

- `parduodami-sklypai`. Bare land is a different purchase and needs
  property-type scoring to rank honestly. Deferred behind Plan B.
- `backend/app/classify.py` and per-type criteria (v2 §5). Both categories here
  are buildings with land, so the existing ten criteria and six hard flags apply
  unchanged. Building a type system for a single effective type is speculative.
- Any change to `registry.py`'s policy model. See §4.
- Any new source. The other five POLL sources were surveyed on 2026-08-11:
  `ntaukcionai` is dead (all four categories empty, footer © 2008-2021),
  `adminbiuras` sells industrial equipment and debt claims, `zudc` has no
  listing type, `turtas` publishes only rentals, `data_gov` is an open-data API.

## 4. The lawfulness gate does not fragment

`sources/registry.py` stays keyed by **host**, because robots.txt is a property
of the host: `www.rinka.lt` carries one policy whether the path is sodybos or
namai. `assert_pollable("rinka")` keeps its present meaning, and the crawl delay
stays a per-source declaration.

Categories are an **adapter** concern. This boundary is the reason a second
category costs almost nothing to add and cannot weaken the policy check.

## 5. Design

### 5.1 Adapter

`adapters/rinka.py` replaces the module-level `CATEGORY` constant with a
declared mapping of category key to path, and `list_url()` gains the category:

```
CATEGORIES: dict[str, str]        # "sodybos" -> "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos"
                                  # "namai"   -> "/nekilnojamojo-turto-skelbimai/parduodami-namai"
list_url(category: str, page: int = 1, per_page: int = 200) -> str
```

`list_ids` and `parse_detail` are category-agnostic and are **not** touched.
That includes `_content()`, whose two fix rounds (heading-boundary slicing, and
bounding at the first link to a different listing id) are the file's most
fragile earned knowledge.

An unknown category key raises, rather than silently falling back to sodybos.

### 5.2 Cursors — the part that must be right

`source_cursor`'s primary key is `source`. One high-water mark across two id
streams lets the higher one suppress the other: a `namai` listing numbered below
the sodybos watermark would be filtered out by `if i_u[0] > since` and never
retried. That is the *silently lose a listing* failure this project ranks second
only to showing a confident wrong answer.

SQLite cannot alter a primary key, and migrations here are additive only, so
this is a new table:

```sql
CREATE TABLE IF NOT EXISTS source_category_cursor (
    source    TEXT NOT NULL,
    category  TEXT NOT NULL,
    last_id   TEXT,
    etag      TEXT,
    modified  TEXT,
    polled_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source, category)
);
```

Seeded in the same migration from the existing `source_cursor` row for rinka as
`('rinka', 'sodybos', last_id, …)`, so it resumes at its current position rather
than re-walking 86 listings. rinka is the only source that has ever held a
cursor; if another row exists, the migration leaves it alone rather than
guessing which category it belonged to, and that source then starts from zero
when an adapter declares its categories. `source_cursor` is left in place,
unread by the new path — dropping it is not additive and buys nothing.

### 5.3 Poller

`poll_source(key)` iterates the adapter's categories. Per category it walks
pages from 1 and stops at whichever comes first:

- **a page adds no ids the walk did not already have** — the end of the
  category. This is a healthy, complete walk and the cursor may advance;
- a page carries **no listing links at all** — not the end of anything (see
  below). Treated as a stall: the cursor is withheld and the run reports it;
- `POLL_MAX_PAGES` (`SR_POLL_MAX_PAGES`, default 5) is reached — likewise a
  stall, for the same reason, and likewise reported.

What the pages actually look like, measured against the live site 2026-08-12
at `per_page=200`:

| category | page 1 | page 2 | page 3 |
|---|---|---|---|
| `sodybos` | 96 ids | 10 ids | the same 10 ids |
| `namai` | 208 ids | 162 ids | 10 ids |

Two facts follow, and both matter:

1. A category's own ids **descend** across pages.
2. Every page — including one past the end of the category — also renders a
   fixed block of the site's ~10 **newest** listings. So a page past the end is
   neither empty nor lower-numbered; it is the top of the id range.

Fact 2 is why the stop condition is "no new ids" and not "no ids above the
cursor": the latter never fires against the real site, and every category
walked to `POLL_MAX_PAGES` on every run. It is also why the listing-free check
reads the **unfiltered** ids — a page whose listings all sit at or below the
cursor is the ordinary steady state, not a failure.

**Deliberately not a stop condition:** "`POLL_MAX_PER_RUN` fresh ids collected,
so stop fetching list pages". An earlier draft of this section specified it. It
loses listings, because by fact 1 the later pages hold exactly the ids an
oldest-first batch processes *first* — the run picks its batch off page 1 and
then advances the watermark above everything the unfetched pages held.
Simulated against `namai`'s real shape (352 listings, `per_page=200`,
`limit=40`) it lost 152 listings, 43% of the category, on the first sweep,
while reporting `status: ok`. List pages are cheap and the walk is already
capped; collect every page, then take the oldest `limit`.

`POLL_MAX_PER_RUN` (40) applies **per category** to *detail* fetches, so a full
pass costs at most 80 detail fetches plus a handful of list pages at rinka's
declared 2 s delay — under three minutes per hourly run. The first sweep of
`namai` reaches its newest listing in roughly nine runs; it is bounded by
`POLL_MAX_PER_RUN`, not by pagination.

The contiguous-advance rule is unchanged and now applies per category: the
cursor advances only across an unbroken run of successes, so a mid-batch failure
is retried rather than skipped. This is existing behaviour that the per-category
split must preserve, not re-derive.

### 5.4 Provenance

`candidate` gains `source_category TEXT` (additive, nullable). Existing rows
stay `NULL` rather than being back-labelled `sodybos` — they were ingested
before categories existed, and inventing provenance for them would be the same
confident-wrong-value failure the project exists to avoid.

### 5.5 Cross-category dedupe

One homestead can be listed under both categories with different listing ids.
`dedupe.py` already fingerprints on price, area and locality with 4-character
Lithuanian stemming, and `mailbox._insert` already folds a duplicate into its
twin — but neither has ever seen two rows from the same source under different
categories, because that could not previously happen.

No new mechanism. The requirement is that the existing one is **exercised and
proven** for this case, including that the surviving row keeps the notes trail
(`E2`/`E3` in the Plan A deferred findings) so a wrong merge stays visible.

## 6. Error handling

- Unknown category key → raise, do not default.
- A category's list page returning non-200 → that category is skipped for the
  run, its cursor untouched, and the failure counted in the run summary. Other
  categories still run: one failure must not stop the rest, matching
  `poll_all`'s existing contract.
- A detail page failing mid-batch → existing behaviour: counted, cursor stalls
  at that id, retried next run.
- `POLL_MAX_PAGES` reached → the walk stopped part-way down a descending list,
  so the pages it never reached hold ids *below* everything it did fetch and
  the cursor cannot be advanced without burying them. Expressed as a stall at
  the lowest fetched id, so the contiguous-advance rule already withholds the
  watermark; what was fetched is still ingested. The cap is a safety net, not
  an operating limit (5 × 200 = 1000 against a largest real category of 372),
  so the run says plainly that the cursor did not move and names
  `SR_POLL_MAX_PAGES` — a category permanently over the cap makes no progress
  at all, which is the right trade only if it is loud.
- A 200 carrying no listing links → same treatment, same reasoning. Per §5.3
  fact 2 a real page always carries some, so this is a rate limiter, a
  maintenance notice, or a render the parser does not understand.
- Every category's list page failing → the run itself reports `error`, not `ok`
  with the failures buried in the detail text: `/api/ingest/log` surfaces the
  status column, and a sweep that fetched nothing must not read as healthy.
  Partial failure stays `ok`; the `categories` map names what failed.

## 7. Testing

All against an injected fetcher; no network in tests.

- `list_url` builds the right path per category; an unknown key raises.
- Two categories with **overlapping id ranges** each advance their own cursor,
  and a `namai` id below the sodybos watermark is still ingested. This is the
  test that pins §5.2; without it the whole change is unproven.
- Migration: a database holding a `source_cursor` row for rinka gains the
  seeded `('rinka','sodybos')` row with the same `last_id`, and running the
  migration twice is a no-op.
- Pagination: page 2 is fetched when page 1 is full of new ids, and the batch
  is the oldest ids of the **whole** sweep, not of page 1; walking stops when a
  page adds nothing new; a page past the end of the category (the newest-listings
  block alone) ends the walk and lets the cursor advance; a page whose listings
  all sit at or below the cursor does the same and is not mistaken for a failure.
- Neither exit that stops the walk early may advance the cursor: `POLL_MAX_PAGES`
  and a listing-free 200 each leave the watermark where it was, still ingest what
  was fetched, and say so in the run summary.
- `source_category` is asserted on a row stored by `poll_source` itself, not
  only on one handed to `_insert` directly — that is the only path that writes it.
- A category list page failing leaves that cursor untouched while the other
  category still ingests.
- Cross-category dedupe: the same property under both categories yields one
  candidate, with the discarded listing's price, size, title and URL in notes.
- `source_category` is stored for new rows and stays `NULL` for pre-existing.
- Crawl delay is still read from the registry and awaited per fetch — `B4` in
  the deferred findings notes this has no assertion today; add it here, since
  this change doubles the request volume against a site that permits crawling.

## 8. What this does not deliver

Stated so it is not mistaken for a finished product:

- It does not tell you whether a property can be bought. Every hard flag remains
  undetectable from listing prose.
- It does not improve location precision. Every candidate stays at Tier C, a
  settlement centroid ±1 km.
- It does not add sources. The five other POLL sources are surveyed and empty
  of relevant stock; the six portals holding real inventory are ALERT_ONLY and
  reachable only through `docs/EMAIL_ALERTS.md`, which needs an operator mailbox
  before it can run.

## 9. Next

**Plan B — location precision and diagnosis.** The cadastral number and parcel
geometry, then the open registries that answer *why has this not sold*:
Registrų centras for ownership and encumbrances, Mel_DR10LT for drainage
condition, the forest cadastre, and protected-area boundaries. It is the same
key the farming sub-score
(`2026-08-10-farming-suitability-design.md`) has been gated on, so one plan
unlocks both. That is the next spec.
