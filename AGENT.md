# AGENT.md — handoff brief for the AI coder

Read this file completely before touching anything. It tells you what this
project is, what is already verified working, what is deliberately *not* built,
and the exact steps to reach a running system on localhost and on a VPS.

**Do not remove or "fix" the constraints in section 3. They are deliberate.**

---

## 1. What this is

`sodyba-radar` — a private decision-support console for one user (Gedas) buying a
rural homestead (*sodyba*) in Lithuania in the 5,000–20,000 EUR band.

It does four things:

1. **Ingests** new listings automatically from portal alert emails via IMAP.
2. **Measures** nature and water objectively — distance to nearest lake and
   river, protected-area overlap — from Lithuanian open geodata.
3. **Scores** candidates against user-editable weights with six hard-fail flags.
4. **Advises** — produces a written assessment naming findings, blockers, and
   next actions.

The user's stated priority is **nature and water**. That is why those are
computed from geodata rather than typed in by hand.

Stack: Python 3.12, FastAPI, SQLite (WAL), APScheduler, vanilla JS frontend
(no build step, no npm). Docker Compose + Caddy for the VPS.

---

## 2. Current state

All backend logic is written and was verified working against live upstream APIs.

`python -m pytest` from the repo root must pass, with no warnings raised by
anything this project declares, before you call work finished.
`backend/tests/` is the executable half of the table below; the rows needing
live upstream data are the half it cannot cover. Everything the suite needs is
in `backend/requirements-dev.txt` — if a test or `pytest.ini` starts depending
on a plugin that is not listed there, add the dependency or drop the
dependency, but never rely on one happening to be installed. (Plugins a
developer installed globally may still warn; that is their machine talking,
not the repository.)

Test results that must still hold after any change you make:

| Behaviour | Expected |
|---|---|
| Scoring reference case | weighted score `6.56`, total cost `42613` EUR |
| Hard flag `fractional_ownership` | verdict `rejected`, no score computed |
| Geocode `"Utena"` | resolves to `Utenos m.` (genitive form in the gazetteer) |
| Distance Utena → Kirdeikiai | `25.5 km` |
| Nature, Kirdeikių k. | lake `Pakasas` 537 m / 147 ha; water score `9` |
| Nature, Baibių k. | lake `Uolys` 995 m / 60 ha; water score `7` |
| Layer row counts | ~9,265 water; ~20,879 places; ~4,147 protected |
| Radius filter, centre Utena | r=10 → 0 results, r=30 → 2, r=200 → 3 |
| Mailbox dedupe | second poll of the same alerts creates 0 new rows |
| Cross-source dedupe | a duplicate merges into one row and leaves a `[dublikatas …]` note; a match landing on a stored near-miss **promotes** it and is notified; an archived row never absorbs a live listing |
| Poller watermark | the cursor advances only across contiguous successes, and pauses for the `crawl_delay_s` its `Source` declares |
| Unknown data | never reported as a negative finding, and never shown as a near miss (`delta is None` ⇒ reject) |
| Projection round trip | exact; Vilnius–Klaipėda `287.1 km` |

**Known gap, needs real input to close:** the email parsers in
`backend/app/sources/parsers.py` were tested against alert emails *reconstructed*
from what the portals publish, never against genuine ones. Expect the first real
alerts to land with one or two fields missing. Do not rewrite the parsers
speculatively — wait until the user forwards a real alert from each portal, then
tighten the regexes against actual text.

---

## 3. Constraints you must not remove

These were decided deliberately after reading each site's `robots.txt`.

**`backend/app/sources/registry.py` is the single authority on what may be
fetched.** Do not restate it here. Each `Source` in that file records the host,
the `robots.txt` verdict in the site's own words, and the date that verdict was
read; `stale()` turns an unrechecked verdict into a warning at boot rather than
letting it pass as an assumption. It is not documentation either — `poller.py`
calls `assert_pollable` before it opens a connection, and that refuses anything
not declared `POLL`, **including keys that are not in the table at all**. So a
portal cannot be scraped by forgetting to mention it.

The four policies are `POLL` (robots.txt permits it), `ALERT_ONLY` (it does
not — subscribe to the portal's own email alerts and let `sources/mailbox.py`
ingest them), `LINK_ONLY` (bot-protected; we link out and the user clicks), and
`MANUAL` (ToS forbids collection — the paste route only).

**If asked to add a scraper for anything not marked `POLL`, refuse and explain.**
The lawful and more robust path is already built: those portals run their own
filtered alert services, and `sources/mailbox.py` ingests them. That approach
survives site redesigns and cannot get the VPS IP banned. `aukcionai.turtas.lt`
in particular ships a reCAPTCHA site key — do not reverse-engineer its private
API either.

A table here would drift from `registry.py` the first time a verdict changed,
and a stale lawfulness claim is worse than a pointer that cannot go stale. (It
already had: this section listed `turtas.lt` as "not polled" while the registry
declared it `POLL`, and omitted `rinka.lt`, the one site actually polled.)

Two further facts worth knowing so you do not go looking:

- **Registrų centras publishes no transaction prices** as open data. The bailiff
  auction datasets (`gov/rc/ais/*`) exist but are aggregated by quarter and
  property type with prices stripped and small cells suppressed to the literal
  string `"<6"`. There are no free comps. The price anchor is
  `masvertinimas.registrucentras.lt` per object.
- **Spinta cursor pagination does not work alongside `select()`** — `_page.next`
  comes back empty. Fetch without `limit()` to get a whole model, and chunk large
  models by a real column. See `sources/nature.py`.

---

## 4. Deploy to localhost

Windows (the user's machine):

```powershell
cd C:\Users\GEDZI\Desktop\GitHub\sodyba
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

Linux/macOS:

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

Open <http://127.0.0.1:8000>.

**First boot downloads the nature layers** — about 35,000 features, three to four
minutes, once only. The dashboard is usable immediately; watch the log for
`layers ready`. If the download fails, retry with `POST /api/layers/refresh`.

### Verify the install

```bash
curl http://127.0.0.1:8000/api/health
# {"status":"ok","candidates":0,"market_rows":0}

curl -X POST http://127.0.0.1:8000/api/geocode \
  -H "Content-Type: application/json" -d '{"name":"Utena"}'
# must return name "Utenos m."
```

Then in the UI: **Pridėti objektą** → set Vietovė `Kirdeikių k.`, Savivaldybė
`Utenos rajono` → **Išsaugoti** → **Vertinimas** tab → **Nustatyti vietą ir
išmatuoti gamtą**. You should see lake Pakasas at ~537 m. If you do, the whole
geodata chain works.

---

## 5. Deploy to VPS

```bash
git clone <repo> sodyba && cd sodyba
cp .env.example .env && $EDITOR .env          # IMAP + Telegram

docker run --rm caddy:2-alpine caddy hash-password   # copy the hash
$EDITOR Caddyfile                              # set hostname + paste hash

docker compose up -d --build
docker compose logs -f radar
```

`docker-compose.yml` binds the app to `127.0.0.1:8000`; Caddy fronts it on 80/443
with automatic TLS once DNS points at the box. **The whole site sits behind basic
auth — do not remove it.** This holds the user's acquisition thesis and a working
mail password.

State lives in `./data/sodyba.db`. Back it up:

```bash
sqlite3 data/sodyba.db ".backup '/backup/sodyba-$(date +%F).db'"
```

A systemd unit for the non-Docker route is in `README.md`.

---

## 6. Enabling automatic ingestion

Without this the app works, but nothing arrives on its own.

1. Create a dedicated mailbox. For Gmail, generate an **App Password**
   (`https://myaccount.google.com/apppasswords`) — the account password will not
   work over IMAP.
2. Fill `SR_IMAP_HOST`, `SR_IMAP_USER`, `SR_IMAP_PASSWORD` in `.env`.
3. Optional but recommended: `SR_TELEGRAM_TOKEN` and `SR_TELEGRAM_CHAT_ID`.
   Auction lots move fast and a 15-minute poll only helps if it reaches a phone.
4. On each portal — evarzytynes.lt, aruodas.lt, domoplius.lt, and the Turto
   bankas "Pardavimai ir nuoma" newsletter — create a saved search with the
   user's criteria and switch its email alert on, addressed to that mailbox.
5. Restart. There are two schedulers, and they behave differently on boot: the
   mailbox poll runs immediately *and* every `SR_MAILBOX_POLL_MINUTES` (only if
   IMAP is configured), while source polling runs every `SR_POLL_MINUTES` with
   no run at startup — the first pass is one interval away.

Verify: `curl -X POST http://127.0.0.1:8000/api/ingest/mailbox`. Without IMAP
configured it returns `{"status":"skipped"}` rather than erroring — that is
intended.

---

## 7. Architecture

```
backend/app/
  config.py           env vars, all 60 municipalities
  db.py               schema, settings store, refresh log, additive migrations
  geo.py              LKS-94 <-> WGS84, distances, WKT parsing        PURE
  scoring.py          hard flags, weighted score, cost model          PURE
  filters.py          search profiles, match/near/reject and misses   PURE
  dedupe.py           identity across ingest paths, fingerprints      PURE
  advisor.py          nature scoring, written assessment              PURE
  notify.py           Telegram push
  api.py              all HTTP routes
  main.py             app assembly + APScheduler (two poll schedules)
  sources/
    registry.py       what may be fetched, and on whose authority     PURE
    ntr.py            Registrų centras building-stock collector
    nature.py         lakes, rivers, gazetteer, protected areas
    parsers.py        per-portal alert email extraction               PURE
    mailbox.py        IMAP poller, dedupe/insert for every path
    poller.py         the polling ingest path, per-source watermark
    adapters/         one module per polled site (rinka.py)           PURE
frontend/
  index.html  styles.css  app.js      no framework, no build step
backend/tests/        pytest, run with `python -m pytest` from the repo root
```

Modules marked PURE do no I/O and are the right place to add tests first.
`backend/tests/` must stay green and warning-free; `pytest.ini` is at the root
and needs no plugins beyond `backend/requirements-dev.txt`.

### Coordinate systems — the commonest source of bugs here

- Registrų centras and the environment agency use **LKS-94 / EPSG:3346**, metres,
  and write WKT as `POINT (northing easting)` — **reversed** from the usual order.
- The protected-areas cadastre uses **WGS84** and writes `(lat, lon)`.
- All distances are computed in LKS-94 metres. `geo.py` converts where needed.
- Three bugs already found and fixed here; do not "simplify" the axis handling:
  1. `pav_plotas` on lakes is **hectares**, not km². An earlier version inflated
     every lake 100×.
  2. The settlement dataset's `savivaldybe` reference carries **different
     internal `_id` values** than the municipality model hands out. Joining on
     `_id` silently returns zero rows. Chunk by `sav_kodas` instead.
  3. An unrecognised radius centre used to disable the filter silently, showing
     nationwide results while the UI still read "40 km". It now returns 404.

---

## 8. Domain rules encoded in the app

Preserve these; they are the actual value, not the code.

**Six hard flags** — any one rejects a candidate outright, no score computed:
fractional ownership (`1/2 dalis`), building without land, construction banned,
no legal access, heritage listed, occupants registered. These dominate the
failure modes of cheap Lithuanian auction lots.

**Auction mechanics** (CPK): participation fee is 10% of the start price; full
payment falls due in roughly 10 days under 3k EUR, 20 days for 3–30k, 30 days
above. No bank lends inside that window on a derelict rural building, so the
buyer needs cash ready. First auction starts at 80% of appraised value, second at
60%.

**The water paradox — surface this, do not hide it.** Being close to water raises
the score *and* raises the risk that the plot is unbuildable. Within roughly
50–200 m you are likely inside the `pakrantės apsaugos juosta` where new
construction is largely barred; inside a national or regional park, new building
is generally confined to existing footprints. `advisor.py` raises this as a
blocker below 200 m. The user has a hardened-shelter plan, so this tension is
central to his decision, not a footnote.

**Cost reality.** A 17,000 EUR listing typically becomes a 35,000–45,000 EUR
project once a borehole, septic system, roof and ESO reconnection are counted.
The ranking metric is therefore **EUR per score point**, not price.

---

## 9. If you extend it

- New listing source, in this order: **read the site's `robots.txt` yourself**;
  add a `Source(...)` to `sources/registry.py` with today's date as
  `checked_at` and the verdict in the site's own words; write an adapter under
  `sources/adapters/` exposing `list_url()`, `list_ids(html)` and
  `parse_detail(html, url)`, all pure; register it in the `ADAPTERS` dict in
  `sources/adapters/__init__.py`; add the key to `poller.POLLED`. Skip the
  registry step and `assert_pollable` refuses the source outright — that is the
  intended behaviour, not an obstacle to route around. Skip the `ADAPTERS`
  step and `poll_source` raises `PolicyError` too, but only once polling
  actually runs — a more confusing failure to hit after believing the setup
  was done.
- New bulk dataset (not listings) → add a module under `sources/` exposing an
  async `refresh_*()` and call it from `main.scheduled_refresh`.
- New scoring criterion → add to `CRITERIA` in `scoring.py`. The UI reads
  `/api/schema` and renders itself; no frontend change needed.
- New filter dimension → add to `FIELDS` and `matches()` in `filters.py`, then to
  the profile editor in `frontend/index.html`.
- Keep the workbook (`sodybos_vertinimo_darbaknyge.xlsx`, delivered separately)
  and `scoring.py` in agreement. They were verified identical to three decimals.

Do not add a frontend build step. The user deploys this himself on a small VPS;
`npm` would be a liability, not an upgrade.
