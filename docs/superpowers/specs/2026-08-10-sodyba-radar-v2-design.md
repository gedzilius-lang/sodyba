# Sodyba Radar v2 — design

Date: 2026-08-10
Status: approved in outline, pending review of this document

---

## 1. Why v2

v1 works as designed. Three things limit what it can tell you:

1. **Every measurement descends from a settlement centroid.** `advisor.assess_nature`
   geocodes the locality and measures from there, stating ±1 km in its own note.
   A candidate reading *water 9/10* may truly be 4/10 and the screen cannot say which.
2. **One ingest path, and it is untested.** `sources/parsers.py` carries a KNOWN GAP
   comment: extractors have only met reconstructed emails. Until real alerts arrive,
   nothing flows.
3. **Filters discard silently.** `filters.matches()` returns on the first failed check
   and non-matching listings never reach the table, so a listing 5% over the price
   ceiling vanishes without trace.

v2 addresses all three, broadens the property thesis beyond restorable homesteads,
and adds the two surfaces that make the data usable away from a desk.

Non-negotiables carried forward from `AGENT.md`: no build step, no npm, modest VPS,
`robots.txt` obeyed, PURE modules stay pure.

---

## 2. Scope

**In scope for v2**

- Location resolution ladder with confidence tiers (§3)
- Buildability verdict from real geometry (§4)
- Property-type-aware classification and scoring (§5)
- Near-miss tier in filtering (§6)
- Polling ingest for permitted sources, alongside existing alerts (§7)
- Map surface (§8)
- Field mode (§9)

**Explicitly out of scope for v2**

- Telegram forward-bot for Facebook listings. Facebook and any other manual source
  go through the existing `POST /api/paste` in the web UI. Decided 2026-08-10.
- Scraping any source whose `robots.txt` forbids it. Unchanged, permanent.
- MCDA methods (TOPSIS/PROMETHEE) and weight sensitivity analysis. Deferred to v3.
- Road-network travel times, DEM, solar yield. Deferred to v3 (§12).

---

## 3. Location resolution ladder

### Problem

`assess_nature` accepts `easting`/`northing` if present, otherwise geocodes
`locality` + `municipality` to a `place` row — a settlement centroid. One path,
one precision, and the resulting note is attached to prose rather than to data.

### Design

Resolution is attempted in descending order of precision. The tier that succeeded
is stored on the candidate and travels with every derived number.

| Tier | Resolved from | Geometry stored | Stated error |
|---|---|---|---|
| `A_PARCEL` | `cadastral_no`, or address with house number | parcel polygon | metres |
| `B_STREET` | street named, no house number | street centreline | 100–300 m |
| `C_PLACE` | locality name (v1 behaviour) | centroid point | ±1 km |
| `D_MUNI` | municipality only | municipality centroid | ±20 km |
| `NONE` | nothing usable | — | not ranked |

`candidate.cadastral_no` already exists in the schema and is already extracted by
`parsers.CAD_RE`. It is currently unused for location. It becomes the Tier A key.

### Source

[govlt/national-boundaries-api](https://github.com/govlt/national-boundaries-api) —
Centre of Registers Address Registry: counties, municipalities, elderships,
residential areas, streets, addresses, rooms and **parcels**, with geometries and
SRID transforms. Ships as one Docker image over a SQLite file under 500 MB.
MIT code, CC BY 4.0 data.

Deployment: run alongside the app in `docker-compose.yml` as a sidecar, queried over
localhost HTTP. Rejected alternative: importing its SQLite directly into
`sodyba.db` — faster, but couples us to their schema and loses their update path.

### Rules

- **No number renders without its tier.** The API returns tier alongside every
  measured distance; the frontend renders a tier chip next to it.
- **The advisor's sentences change with tier.** Tier A: "the plot's nearest edge is
  180 m from Pakasas." Tier C: "the village centre is 537 m from Pakasas; the plot
  lies somewhere within roughly a kilometre of that."
- **Sorting and filtering by tier**, so "only what I can trust" is one control.
- **Tier never silently degrades.** If a Tier A resolution later fails to reproduce,
  the candidate is flagged, not quietly demoted.
- **Manual coordinates outrank everything** and are recorded as `A_MANUAL` — same
  principle as hand-set scores never being overwritten.

### Code

New PURE module `backend/app/resolve.py`:

```
resolve(listing, providers) -> Resolution{tier, easting, northing, geometry, source, note}
```

Providers are injected so the module stays pure and testable with fixtures.

`geo.py` gains, still with no third-party dependency:

```
point_in_ring(pt, ring)              ray casting
dist_point_to_ring(pt, ring)         0.0 when inside
dist_point_to_polyline(pt, line)
```

Shapely/GEOS/GeoPandas are deliberately not adopted: they would break the
"modest VPS, no build step" constraint for perhaps 120 lines of well-understood
geometry. `geo.py` already hand-rolls the EPSG:3346 projection for the same reason.

### Schema

```sql
ALTER TABLE candidate ADD COLUMN location_tier TEXT;
ALTER TABLE candidate ADD COLUMN parcel_json   TEXT NOT NULL DEFAULT '{}';
```

---

## 4. Buildability verdict

### Problem

`protected_area` stores only a bounding box (`min_e, min_n, max_e, max_n`), so
`protected_hits` returns envelope hits. `advisor.advise` correctly labels these
"apytikslis rėžis, ne riba". Separately a blocker fires when water is within 200 m
— a proxy for the shoreline protection strip. Both are advisory text. Meanwhile
`construction_banned` is a hard flag set by hand, and `buildability` is criterion
#8 scored by hand at weight 0.10.

Nothing connects the measurement to the flag.

### Design

New PURE module `backend/app/buildability.py`:

```
assess(parcel_or_point, layers) -> Verdict{
    state:   CLEAR | RESTRICTED | BLOCKED | UNKNOWN
    reasons: [Reason{layer, text, source, verify_url}]
    confidence: derived from location_tier
}
```

Inputs, all real geometry rather than envelopes:

| Layer | Test | Consequence |
|---|---|---|
| Protected areas (STK) | parcel ∩ polygon | inside NP/RP: new build generally confined to existing footprints |
| Shoreline strip | distance to water body, banded 50–200 m by water size | new construction largely barred |
| Forest cadastre | parcel ∩ forest polygon | *miško žemė*: barred short of changing designation |
| Existing footprint | building present on parcel | decides whether the NP/RP restriction is fatal |

**`UNKNOWN` is the honest result for Tier C and D** and must be visible as such.
A bounding-box guess dressed as a verdict is worse than no verdict.

### Wiring into scoring

- `BLOCKED` sets the existing `construction_banned` hard flag, which
  `scoring.evaluate` already treats as an outright rejection. No change to
  `scoring.py`'s flag logic.
- The verdict populates the `buildability` criterion score unless it was set by
  hand — same override rule as nature scores.
- `advise` gains the verdict's reasons as blockers, each with its verification URL
  (REGIA, STK, masvertinimas) resolved to the specific parcel.

This is the highest-value change in v2. Buying an unbuildable plot is the most
expensive mistake available, and the water paradox in the README means the app's
own scoring actively steers toward it.

### Schema

```sql
ALTER TABLE candidate ADD COLUMN buildability_json TEXT NOT NULL DEFAULT '{}';

CREATE TABLE protected_ring (
    area_id  INTEGER NOT NULL,
    seq      INTEGER NOT NULL,
    ring_wkt TEXT NOT NULL,
    cell_x   INTEGER NOT NULL,
    cell_y   INTEGER NOT NULL
);
CREATE INDEX ix_protected_ring_cell ON protected_ring(cell_x, cell_y);

CREATE TABLE forest_parcel (
    id INTEGER PRIMARY KEY, ring_wkt TEXT NOT NULL,
    cell_x INTEGER NOT NULL, cell_y INTEGER NOT NULL
);
CREATE INDEX ix_forest_cell ON forest_parcel(cell_x, cell_y);
```

`protected_area` keeps its bbox columns as a cheap pre-filter before ring tests —
the same cell-grid technique `water_feature` already uses.

Forest cadastre source: `data.gov.lt` dataset 3779 (Miškų valstybės kadastras)
and geoportal.lt. Also improves §5 and the `forest_water` criterion, which today
infers forest from *municipality lists* in `filters.FOREST_BELT`.

---

## 5. Property-type-aware scoring

### Problem

The thesis broadened beyond 5–20k restorable homesteads. `scoring.py` has one
criteria set, one cost model, and `budget_ceiling_eur` is a single global setting
(default 25 000) driving the `over_budget` verdict. A bare plot scored against
homestead criteria produces nonsense: `condition` and `power` are meaningless,
`building_without_land` is fatal when it should be irrelevant.

### Design

New PURE module `backend/app/classify.py`:

```
classify(listing) -> SODYBA | SKLYPAS_NAMU | SKLYPAS_ZU | MISKAS | NAMAS | REJECT
```

Each type carries its own criteria weights and cost lines. All emit the same
comparable output: **EUR per score point** on a shared 0–10 scale, so a 12k ruin
and a 30k plot rank in one list.

Hard flags stay universal with one change: `building_without_land` becomes
type-conditional — fatal for `SODYBA`, irrelevant for the plot types. This is the
specific bug the broadened thesis introduces.

`budget_ceiling_eur` moves from a global setting to **per-profile**, since a
profile already carries `min_price`/`max_price`. The global remains as fallback.

`MISKAS` and `SKLYPAS_ZU` are carried but with simplified cost models — they are
classify-and-rank targets, not the primary thesis. Assumption, flagged for review.

---

## 6. Near-miss tier

### Problem

`filters.matches()` returns `(bool, reason)` and returns on the **first** failed
check. A listing failing on price alone is indistinguishable from one failing on
six things, and per the README a listing matching no profile "is discarded and
never reaches the table."

Consequence: a 21 000 EUR sodyba against a 20 000 ceiling is destroyed at ingest,
and the filters never learn they were wrong.

### Design

`matches()` is replaced by an evaluator that runs **every** check and classifies
each failure:

```
evaluate(listing, profile) -> ProfileMatch{
    state:  MATCH | NEAR | REJECT
    misses: [Miss{field, kind: HARD|SOFT, delta, text}]
}
```

**Hard misses** — structural, unfixable, still discarded and still never stored:
excluded junk words (`dalis`, `1/2`, `butas`, `garažas`), wrong source, wrong
property type. v1's noise control is preserved exactly.

**Soft misses** — numeric boundary or partial keyword hit. Default tolerances:

| Field | Tolerance |
|---|---|
| price | ±25% of the breached bound |
| plot_ares / house_m2 | ±30% / ±25% |
| max_lake_m / max_river_m | ×1.5 |
| radius_km | ×1.3 |
| municipality | adjacent municipality |

A listing whose only failures are soft, all within tolerance, becomes `NEAR`.

**`require_any` becomes named groups.** Today it is one flat list, so
`FOREST_WORDS` and `WATER_WORDS` cannot be scored separately. As groups, a listing
hitting forest but not water is a soft miss reading `miškas ✓ / vanduo ✗` — the
requested case — instead of a silent rejection.

### Surfacing

- Dashboard gains three tiers: **Atitinka** · **Beveik** (collapsed by default) ·
  discarded. Each near-miss row states what it missed and by how much:
  `kaina 21 000 EUR — 1 000 virš ribos`, `miškas ✓ / vanduo ✗`.
- One click accepts a near-miss into the main list, or widens the profile.
- **Telegram fires only on `MATCH`.** Near-misses would flood it.
- **Calibration loop:** when a threshold accounts for a large share of recent near
  misses, the profile editor says so and offers the adjustment. This is how the
  filters — currently guesses — get corrected by the market.

Near-miss retention: archived after 30 days, configurable.

### Schema

```sql
ALTER TABLE candidate ADD COLUMN match_state TEXT NOT NULL DEFAULT 'match';
ALTER TABLE candidate ADD COLUMN misses_json TEXT NOT NULL DEFAULT '{}';
```

---

## 7. Ingest: three paths, policy enforced in code

### Source policy

`robots.txt` verdicts, all checked 2026-08-10:

| Source | Verdict | Policy |
|---|---|---|
| `get.data.gov.lt` | `Allow: /` | POLL (existing) |
| `rinka.lt` | `User-agent: *` / `Disallow:` — fully open | **POLL (new)** |
| `zudc.lt` (state land auctions; `aukcionai.vzf.lt` redirects here) | `Disallow:` empty | **POLL (new)** |
| `ntaukcionai.lt` | `Allow`, `Crawl-delay: 10` | **POLL (new)**, 10 s delay |
| `adminbiuras.lt` (bankruptcy estates) | `Allow: /` | **POLL (new)** |
| `turtas.lt` | open | **POLL** (was LINK_ONLY) |
| `aukcionai.turtas.lt` | ships reCAPTCHA site key | LINK_ONLY |
| `evarzytynes.lt` | `Disallow: /` | ALERT_ONLY |
| `aruodas.lt`, `domoplius.lt` | bot challenge on `/robots.txt` | ALERT_ONLY |
| `kampas.lt` | `/robots.txt` returns **403** | ALERT_ONLY |
| `skelbiu.lt` | `Allow: /` but disallows `/select/` and search params; blocks `anthropic-ai`, `Claude-Web` by name | ALERT_ONLY |
| `alio.lt` | disallows `/public/textSearch/*`, `/public/category/search*`; blocked GPTBot/ClaudeBot/Amazonbot July 2026 citing load | ALERT_ONLY |
| Facebook groups / Marketplace | ToS forbids automation | MANUAL |
| `nzt.lrv.lt`, 60 municipal auction pages | **not yet verified** | TBD — changedetection.io watches, not 60 parsers |

skelbiu and alio permit plain category pages; only search paths are disallowed.
They have nonetheless named AI agents in `robots.txt`, and both operate the email
alert channel the app is already built around. Take the alerts. `parsers.ROUTES`
already carries `alio.lt`, `skelbiu.lt` and `rinka.lt` entries — the email side is
wired, only untested.

### Enforcement

New PURE module `backend/app/sources/registry.py`:

```
SourcePolicy = POLL | ALERT_ONLY | LINK_ONLY | MANUAL

SOURCES = [ Source{key, host, policy, robots_verdict, checked_at, crawl_delay_s}, ... ]
```

- The poller **cannot** fetch a source not declared `POLL` — enforced at the fetch
  boundary, not by convention.
- A `checked_at` older than 90 days raises a warning on boot. alio's AI blocks are
  dated July 2026, one month before this document: `robots.txt` changes, and a
  registry that records when you last looked is the difference between a policy
  and a good intention.

Today this rule lives only in `AGENT.md` prose.

### Poller

New `backend/app/sources/poller.py` (I/O). `config.py` already provides `HTTP_UA`
and `REQUEST_DELAY`; per-source `crawl_delay_s` overrides the global.

- Sitemap- or category-page driven, conditional GET via ETag/Last-Modified.
- Per-source backoff; failures land in the existing `refresh_log` table.
- Runs under the existing APScheduler in `main.py`.
- Extracted listings enter the **same** pipeline as email alerts — one dict shape,
  one dedupe, one scorer.

**The poller removes the v1 blocking dependency on untested email parsers.**
rinka.lt is pollable today, so real listings flow from day one and the alert
parsers get calibrated whenever genuine emails arrive.

### Cross-source dedupe

`candidate.fingerprint` has a unique index but is exact-match. With three ingest
paths one sodyba will legitimately arrive three times. Add similarity matching on
(price, plot_ares, house_m2, municipality, locality) with tolerance, plus title
token overlap — following the pattern in
[Fredy](https://github.com/orangecoding/fredy). Duplicates merge into one
candidate carrying multiple source URLs.

---

## 8. Map

MapLibre GL JS + PMTiles, loaded from CDN. No build step, no npm — the constraint
holds. New `frontend/map.js`.

- Candidates as pins: colour = verdict, size = EUR per score point.
- Layers: lakes, rivers, protected areas, forest cadastre, parcel outline at Tier A.
- Click a pin, get the core sample.
- Draw a polygon to filter candidates — a better instrument than typing
  "Utena, 40 km", and complementary to the existing `centres`/`radius_km` controls.

---

## 9. Field mode

A phone-shaped route at `/f/{id}`. New `frontend/field.js`.

- Verdict, buildability state, and the three findings most likely to kill it.
- Gate-side checklist writing to the existing `checks_json`.
- Photo capture.
- Hand-set score overrides — v1's rule that manual scores are never overwritten is
  what makes this worth building.
- Visit loop: shortlist ordered into a driving route.

---

## 10. Module map

```
backend/app/
  resolve.py        location ladder                        PURE  new
  buildability.py   CLEAR/RESTRICTED/BLOCKED/UNKNOWN        PURE  new
  classify.py       property type                           PURE  new
  filters.py        near-miss evaluator                     PURE  changed
  geo.py            + point-in-ring, point-to-ring          PURE  changed
  scoring.py        + per-type criteria, per-profile budget PURE  changed
  advisor.py        + tier-aware prose, verdict blockers    PURE  changed
  sources/
    registry.py     source policy                          PURE  new
    poller.py       polite fetcher                                new
    boundaries.py   addresses + parcels sidecar                   new
    forest.py       forest cadastre                               new
    parsers.py      unchanged until real alerts arrive
frontend/
  map.js, field.js                                                new
```

PURE modules stay pure. Providers are injected. No new runtime dependency beyond
the boundaries sidecar container.

---

## 11. Build order

0. **Poller + source registry.** Unblocks everything: real listings without waiting
   on email.
1. **Location ladder.** Every later number inherits its precision.
2. **Buildability verdict.** Highest value per line of code in the project.
3. **Near-miss tier.** Cheap once `filters.py` is being touched anyway.
4. **Map.**
5. **Property-type scoring.**
6. **Field mode.**

Email parser calibration happens opportunistically whenever real alerts arrive.
It is no longer a gate.

**This spec is too large for one implementation plan.** It decomposes into three,
each independently shippable and each leaving the app working:

- **Plan A — ingest** (steps 0, 3): source registry, poller, near-miss tier, dedupe.
  Touches `sources/`, `filters.py`, the candidate table. No geometry.
- **Plan B — precision** (steps 1, 2): boundaries sidecar, resolution ladder,
  buildability verdict, `geo.py` polygon primitives. Depends on A only for having
  real listings to resolve.
- **Plan C — surfaces** (steps 4, 5, 6): map, type-aware scoring, field mode.
  Depends on B for parcel geometry and tier chips.

Plan A is the one to write first.

---

## 12. Deferred to v3

- MCDA via [scikit-criteria](https://scikit-criteria.quatrope.org/): TOPSIS
  alongside the weighted sum, flagging candidates whose ranking is fragile; weight
  sensitivity ("this stays #1 until water drops below 12%").
- Road-network travel minutes to clinic, shop, school via
  [pandana](https://github.com/UDST/pandana)/[OSMnx](https://github.com/gboeing/osmnx);
  distance to trunk road as a noise proxy.
- Copernicus DEM: slope, aspect, frost hollows, flood-prone dips.
- PVGIS solar yield.
- [Apprise](https://github.com/caronc/apprise) replacing `notify.py`.
- changedetection.io sidecar for municipal auction pages.
- Price anchor where no comps exist — [OpenAVMKit](https://github.com/larsiusprime/openavmkit)
  enrichment, or a model on published aruodas datasets.
- Decision journal: every rejection logged with its reason, to calibrate weights.

---

## 13. Testing

PURE modules are where tests go first, per `AGENT.md`.

Existing reference values must keep passing: the Lazdijai case at score 6.56 and
42 613 EUR total; Utena resolving to the genitive `Utenos m.`; Utena–Kirdeikiai at
25.5 km; layer row counts ~9 265 water, ~20 879 places, ~4 147 protected;
projection round-trip accuracy.

New:

- `resolve` returns the correct tier for each of five fixture listings, and never
  silently degrades a tier.
- `point_in_ring` against a known protected-area polygon, including a point in a
  concavity that a bounding box would wrongly accept — the specific v1 failure.
- `buildability.assess` returns `UNKNOWN` for every Tier C and D input.
- `BLOCKED` sets `construction_banned` and `scoring.evaluate` rejects.
- Near-miss: a listing 5% over the price ceiling is `NEAR`; one containing `1/2`
  is `REJECT` regardless of every other field.
- `require_any` groups: forest-yes/water-no yields exactly one soft miss.
- Registry: the poller raises on a source not declared `POLL`.
- Dedupe: the same sodyba from rinka, aruodas alert, and paste collapses to one
  candidate with three URLs.

---

## 14. Open questions

1. `MISKAS` and `SKLYPAS_ZU` are carried with simplified cost models on the
   assumption that "broader thesis" includes them. Cut either?
2. Near-miss tolerances in §6 are first guesses. They are configurable, and the
   calibration loop is designed to correct them — but the starting values are
   unvalidated.
3. `nzt.lrv.lt` and municipal auction pages are unverified for `robots.txt`.
