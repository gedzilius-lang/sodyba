# Plan A — deferred findings

Everything raised during Plan A's task reviews and judged non-blocking at
the time, with the ruling the final whole-branch review gave each one.
Kept because it is the record of what is still known-wrong: the alternative
is rediscovering these one surprise at a time.

Items A2, A3 and the two Criticals found by the final review were **fixed**
before merge and are struck from the live list; they remain described here
because the reasoning is worth keeping.

Compiled from the SDD ledger after Task 8b. Ranked by consequence, not by the
order they were found. Every item was raised by a task reviewer or by controller
verification, judged non-blocking for its own task, and parked here.

The organising question for each: **can this cause the tool to show a confident
wrong answer, or to silently lose a listing?** Those two failures are what this
project exists to prevent, and they rank above everything else.

---

## A. Real defects, pre-existing, not introduced by this plan

These were all in the code before Plan A began. Two of them became *reachable* or
*consequential* because of work in this plan, which is why they surfaced now.

**A1 — `db.init_db()` cannot migrate an older database.**
`init_db` runs `executescript(SCHEMA)` before the `MIGRATIONS` loop. `SCHEMA`
contains `CREATE UNIQUE INDEX ... ON candidate(fingerprint)`, so on any database
predating the `fingerprint` column it raises
`sqlite3.OperationalError: no such column: fingerprint` and the migration entry
for `fingerprint` can never run. `MIGRATIONS` explicitly lists `fingerprint`,
`easting`, `northing`, `nature_json` and `profiles_json`, which means databases
older than those columns were expected to exist.
*Not reachable for this user* — their database was created by a schema that
already had `fingerprint`. Verified during Task 7.
Fix: move index creation after the `MIGRATIONS` loop.

**A2 — `parsers._common()` mislabels city municipalities as districts.**
It does `muni = MUNI_RE.search(text) or CITY_RE.search(text)` but always formats
the result as `f"{muni.group(1)} rajono"`. So text containing `Vilniaus m. sav.`
is stored as `Vilniaus rajono` — a *different, real* entry in
`config.ALL_MUNICIPALITIES`, used for filtering and the watchlist.
This is the confident-wrong-answer shape: previously `/api/paste` produced
`None` here (honestly unknown), and Task 5b's de-duplication made the city path
reachable from paste as well as from the mailbox. Found by the Task 5b reviewer.

**A3 — the dedupe siblings query has no `archived = 0` filter.**
`mailbox._insert` selects candidate siblings without excluding archived rows, so
a newly arrived listing can be folded into an archived or rejected candidate's
notes and never appear. Untouched by all four Task 8 fix rounds. Found by the
Task 8 final reviewer.

---

## B. Robustness gaps worth a decision

**B1 — `ProfilesIn.require_any` is typed `Any`.**
`PUT /profiles` validates only `profiles: list[dict[str, Any]]`. Task 5 hardened
`normalise_groups` and `sanitise` against malformed input and turned unusable
values into a 400, so the damage is contained — but the root reachability is
untouched by design, since Task 5's scope confined `api.py` changes to the
exception handling.

**B2 — test-suite isolation is fragile.**
`init_db()` uses `CREATE TABLE IF NOT EXISTS` and the session database is never
reset between test modules, so rows from earlier tests persist. The Task 8
dedupe tests avoid colliding with them only because their plot values happen to
differ by 50%. Manually spaced fixture values are standing in for real isolation.
Pre-existing pattern, not introduced here. A per-test transaction rollback or a
fresh database per module would remove the hazard.

**B3 — `list_candidates.match_state` is unvalidated.**
A typo silently yields zero rows rather than an error. Consistent with the
existing convention for `municipality`, `source` and `verdict`, which are equally
unvalidated — so this is a convention question, not a Task 7 regression.

---

## C. Test-coverage gaps

Each of these is a place where a future change could break behaviour that is
currently correct, without any test failing.

- **C1** — no test pins the portal-sniffing price override introduced by Task 5b:
  a paste containing `evarzytynes.lt` plus `pradinė kaina` should yield the
  *starting* price (25000), not the market value (40000). Verified manually only.
- **C2** — no test pins `delta is None → REJECT` in `filters._state`. A future
  reordering of that guard would silently start showing listings with missing
  nature data as near misses.
- **C3** — Task 5's `MIN_KEYWORD_LEN` has no dedicated test for the third
  `normalise_groups` branch (a bare string inside a mixed list), and no
  `sanitise`-level test for `[{"words": ["a"]}]`.
- **C4** — `test_registry` parametrises only `ALERT_ONLY` sources; `LINK_ONLY`
  and `MANUAL` share the same non-`POLL` branch but are never asserted
  non-pollable.
- **C5** — Task 6b's "still misses" regression test uses a present-but-too-far
  lake rather than a genuinely absent one. Equivalent for the gate, but the
  literal "no lake at all" case is not separately exercised.

---

## D. Cosmetic

- **D1** — `.gitignore` lacks `.pytest_cache/`.
- **D2** — `filters.match_all` uses `profile.get("key", "")` where v1 used
  `p["key"]`, turning a loud `KeyError` on a malformed profile into a silent `""`.
- **D3** — `filters.evaluate()` inlines seven checks (~68 lines) while delegating
  three to helpers. Asymmetric, brief-mandated.
- **D4** — `filters._bound_for` returns `int` from its dict branch despite a
  `float | None` annotation. Inert.
- **D5** — `filters._bound_for` re-derives which price bound a `Miss` came from.
  With an unvalidated `min_price > max_price` profile it pairs the low-side miss
  with the high bound. The Task 6 reviewer proved this can only ever err
  *stricter*, never leak a reject into near, so the Telegram guarantee holds.
- **D6** — the Task 4 report's narrative misstates why the radius test hard-misses
  (the listing's municipality fails to geocode, not the profile's centre).

---

## E. Known limits — documented, deliberately not fixed

Not defects. Recorded so nobody "fixes" them without re-reading the reasoning.

**E1 — four-letter Lithuanian nouns do not stem-match.**
`su sodu` against `sodas` stems to `sodu`/`soda` and does not merge, because
short nouns decline in their *fourth* character. Shortening `STEM_LEN` below four
would over-collide. Consequence is a missed duplicate: a row shown twice, which
is visible and harmless.

**E2 — residual same-village dedupe false positive.**
Two different farmsteads in the same named village, both described with the same
incidental amenity words, with price within 5%, floor area within 5% and plot
within 10%, can merge. Accepted deliberately: `STEM_LEN = 5` would fix it but
breaks `ežero`/`ežeras` and `miško`/`miškas`, undoing the reason stemming exists.
Mitigated by Task 8 round 2 — the survivor's notes now carry the discarded
listing's price, size, title and URL, so a wrong merge is visible on inspection.

**E3 — a merged duplicate is recoverable but not noticeable.**
Following E2, the evidence lands in `notes`, which reaches the API and the
per-candidate panel, but no list-view indicator flags a merged row. **This is
assigned to Task 12**, which already renders the row — it is not left open here.

---

## Cross-cutting observation for the reviewer

Three of the defects in this plan shared one shape: a value that was *wrong*
rather than *absent*. A price silently becoming `0.0`; a municipality confidently
labelled `rajono` when it was `miesto`; a bailiff case number standing in for a
cadastral number. In each case the code had a correct-looking value and no way to
know it was wrong.

The codebase's existing instinct is right — `api.py:193` already carries
*"Fail loudly: silently ignoring the filter would show the user nationwide
results while the radius box still reads 40 km."* Items A2 and D2 are both places
where that instinct is not yet applied.

Worth asking during the final review: **which remaining code paths can produce a
confident wrong value rather than an honest `None`?**

---

## Added after Task 12 (final task)

- **C6** — no regression test pins `duplicateChip`'s escaping. It is correct
  today only by manual reading, and it is the one place genuinely
  attacker-influenced text (a portal listing's title and URL, via `notes`)
  reaches the DOM. Ten lines would fix it.
- **D7** — `frontend/app.js` renders `MATCH_LT[c.match_state] || c.match_state`,
  putting the raw value into `innerHTML` unescaped if it is ever outside
  `{match, near}`. Unreachable today — no endpoint can set `match_state` — but
  inconsistent with the file's otherwise universal `esc()` habit, and with the
  adjacent `VERDICT_LT[c.verdict]` which has no fallback.
- **D8** — `frontend/styles.css` uses `var(--muted, #9aa0a6)` but `--muted` is
  never defined; the existing token is `--paper-dim: #8b959d`. The fallback
  always applies, so the near-miss reason text is a slightly different grey from
  every other dim text in the interface. Came verbatim from the plan's own
  snippet.
- **B4** — no test asserts the *value* passed to `asyncio.sleep` in the poller,
  so "the crawl delay is read from the registry" has no coverage. Verified
  manually (three sleeps, each exactly rinka's declared 2.0s). This is a
  lawfulness property toward sites that permit crawling and deserves an
  assertion rather than a controller's word.

## Added after the intake-widening branch

- **A9** — **existing candidate rows may record a category they did not come
  from.** Every rinka list page renders a second block of the site's ~10
  newest adverts alongside the category's own results, and `list_ids` read
  both until 2026-08-13. Two consequences, both pre-dating this branch
  because the block was always on page 1 of `parduodamos-sodybos`:
  - `candidate.source_category`, added by this branch, is wrong for any row
    ingested from that block. It is now correct going forward.
  - Those adverts can be **any** property type — the first entry on the
    sodybos page saved 2026-08-12 was a 215,000 EUR butas. `JUNK_WORDS` and
    the price ceiling reject most of them, but that is a filter compensating
    for an ingestion bug, not the reason the filter exists.

  Not rewritten. Back-labelling would mean re-deriving each stored row's
  category from a page that no longer exists, and guessing is the confident
  wrong value this project ranks first. The rows are few and visible: any
  candidate whose asking price or type looks unlike a homestead is a
  candidate for this. Delete or archive them by hand if they turn up.

- **E9** — the poller tells "this category has no more pages" from "the site
  answered with something else" by a positive landmark: the results-count
  block and the pagination widget that rinka's category controller renders on
  every category page, results or not. Measured 2026-08-13, a request for a
  category that does not exist renders the full site chrome — newest-adverts
  block included — with neither landmark, which is what makes the test work.

  The limit: an error page that reproduced the category's own results
  furniture would be indistinguishable, and would end the walk. No such page
  was observed; rinka answers a missing category with 404 (already refused on
  status) and an out-of-range page with 500. Closing it properly needs a
  second source of truth for "how many results does this category have" —
  the count in that block is one — and that is a bigger change than this
  branch should carry.
