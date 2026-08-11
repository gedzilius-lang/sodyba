# Sodyba Radar

Decision-support console for buying a rural homestead in Lithuania at 5–20k EUR.
Ingests listings automatically, measures nature and water from open geodata,
scores candidates, and writes an assessment.

**Deploying this? Read `AGENT.md` first.** It carries the constraints, the
verified test values, and the exact deployment steps.

---

## What updates automatically, and what cannot

Decided after checking each site's `robots.txt`. Do not change without reading
`AGENT.md` section 3.

| Source | robots.txt | In this app |
|---|---|---|
| `get.data.gov.lt` (Registrų centras, environment agency, protected areas) | `Allow: /` | **Polled.** Official open-data API. |
| `turtas.lt` | `Disallow:` (empty = allow) | Linked, not polled. |
| `aukcionai.turtas.lt` | none, but the bundle ships a **reCAPTCHA site key** | **Not polled.** |
| `evarzytynes.lt` | `Disallow: /` | **Not polled.** Email alerts. |
| `aruodas.lt`, `domoplius.lt` | bot-challenge page even for `/robots.txt` | **Not polled.** Email alerts. |

### The listings robot runs on the portals' own alert channel

Every one of those portals already operates an automated new-listing service with
user-selected filters: their email alerts. Configure the filters once in their
UI, and they push matching listings within minutes of publication. The robot
lives on your side of that channel.

```
evarzytynes.lt saved search  ┐
aruodas.lt saved search      ├→ alerts mailbox ─→ IMAP poll (15 min)
domoplius.lt saved search    │                          ↓
Turto bankas newsletter      ┘              portal-specific parser
                                                        ↓
                                        locate + measure nature
                                                        ↓
                                            your filter profiles
                                                        ↓
                                      dedupe → score → Telegram push
```

Hands off after setup. It never breaks on a site redesign, it is faster than any
polite polling interval, and it cannot get you banned.

**Setup:** create a dedicated mailbox, set `SR_IMAP_*` in `.env` (Gmail needs an
App Password), create the saved searches on each portal pointed at that mailbox,
restart. `SR_TELEGRAM_*` is optional but recommended.

### Also worth knowing

Registrų centras publishes **no transaction prices** as open data. The bailiff
auction datasets exist but are aggregated by quarter and property type with
prices stripped and small cells suppressed to the string `"<6"`. There are no
free comps. Your price anchor is `masvertinimas.registrucentras.lt` per object.

---

## Nature and water are measured, not guessed

Four open datasets, downloaded once and queried offline:

| Layer | Source | Rows |
|---|---|---|
| Lakes and ponds | Aplinkos apsaugos agentūra | ~4,000 |
| Rivers and canals | Aplinkos apsaugos agentūra | ~5,300 |
| Settlement gazetteer | Registrų centras, Adresų registras | ~20,900 |
| Protected areas | Saugomų teritorijų kadastras | ~4,100 |

For any candidate the app geocodes the locality, measures straight-line distance
to the nearest lake over 1 ha and river over 3 km, and tests the point against
every protected-area envelope. Real output:

```
Kirdeikių k., Utenos r.   lake Pakasas   537 m / 147 ha   river Pliaušė  2.7 km
                          4 protected hits incl. Aukštaitijos NP  → water 9/10
Baibių k., Zarasų r.      lake Uolys     995 m /  60 ha   river Ligaja   6.0 km
                          2 hits incl. Gražutės RP               → water 7/10
```

Those become the candidate's default scores. A score you set by hand after
visiting is never overwritten — you saw the place, the dataset did not.

**Two limits, stated in the interface where the numbers appear.** Geocoding
resolves to a settlement *centroid*, so distances carry roughly ±1 km: enough to
rank candidates, not to decide one. Protected areas match by bounding box, so a
hit means "verify at STK and REGIA", not "confirmed inside".

**The tension to watch.** Closeness to water raises the score *and* raises the
chance the plot is unbuildable — within roughly 50–200 m you are likely inside
the `pakrantės apsaugos juosta` where new construction is largely barred, and
inside a national or regional park new building is generally confined to existing
footprints. The assessment raises this as a blocker rather than scoring it as a
win. Kirdeikiai above scores 9/10 on water and sits inside Aukštaitijos national
park: a beautiful place to own and a hard place to build on.

---

## Scope: all of Lithuania, or a radius

Three bounds, combinable:

- **Nationwide** — leave the municipality filter on "Visa Lietuva". All 60
  municipalities are individually selectable.
- **Radius** — type a town and a distance ("Utena", 40 km). The gazetteer handles
  Lithuanian declension: "Utena" finds `Utenos m.`, "Kirdeikiai" finds
  `Kirdeikių k.`. An unrecognised name returns an error rather than silently
  showing you the whole country.
- **Distance to water** — cap kilometres to the nearest lake or river.

Profiles carry the same controls, so a saved search can read "within 40 km of
Utena or Zarasai, lake under 1 km and over 20 ha, 3–20k EUR" and the ingestion
robot applies it to every incoming alert.

---

## Filter profiles

A profile is a saved search the robot applies to every incoming listing. A
listing matching no enabled profile is discarded and never reaches the table.

| Profile | Looks for | Default |
|---|---|---|
| **Miško vienkiemis** | Forest belt (Dzūkija, Aukštaitija, Žemaitija), 3–20k, ≥30 a, ≥40 m² | on |
| **Ežero ar upės pakrantė** | Lake belt, lake ≤1.5 km and ≥5 ha | on |
| **Su infrastruktūra** | The twelve municipalities where utilities actually exist, ranked by the NTR data | on |
| **Varžytynių medžioklė** | Auctions only, nationwide, 1–15k | on |
| **Pigiausias fondas** | Cheapest districts — worse infrastructure, more risk | off |

Geography in each comes from the NTR data, not guesswork: registered plumbing
runs 31.7% of single-family stock in Ukmergė against 8.3% in Šalčininkai, so
"forest" and "utilities" cannot be one profile.

Every profile carries an exclusion list that drops fractional shares (`dalis`,
`1/2`), buildings without land, and apartments before they reach you — the
failure modes that dominate cheap auction lots.

The **Profilis** tab has a dry run: paste any listing and see what got parsed
and, per profile, either PRAEINA or the exact rejection reason.

---

## The scoring model

**Six hard flags.** Any one rejects the candidate outright with no score:
fractional ownership, building without land, construction banned, no legal
access, heritage listed, occupants registered.

**Ten weighted criteria**, each 0–10, weights normalised server-side so a set
summing to 97% or 113% still scores correctly.

**Cost model** with eleven lines plus contingency (15% default; use 25–30% for an
auction lot you could not inspect). The ranking metric is **EUR per score
point** — total project cost divided by weighted score. Sort ascending.

A 17,000 EUR listing typically becomes a 35–45k project once a borehole, septic
system, roof and ESO reconnection are counted. That is why price alone does not
rank anything here.

The dashboard's signature element is the **core sample**: each candidate's score
drawn as a stratigraphy column where segment width is the criterion weight and
opacity is that criterion's score. Move a weight slider and every column
re-stratifies, showing whether a good score rests on things you care about.

---

## Run it

```bash
pip install -r backend/requirements.txt
uvicorn backend.app.main:app --reload --port 8000
```

First boot downloads the nature layers — about 35,000 features, three to four
minutes, once only.

VPS: `docker compose up -d --build`. Set your hostname and a bcrypt hash in the
`Caddyfile` first. Full instructions in `AGENT.md`.

---

## API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/schema` | Criteria, flags, cost lines, checks, municipalities, profiles |
| `GET` | `/api/candidates` | Filter by price, municipality, verdict, score, cost, radius, water |
| `POST` `PATCH` `DELETE` | `/api/candidates[/{id}]` | CRUD |
| `POST` | `/api/candidates/{id}/locate` | Geocode and measure nature |
| `GET` | `/api/candidates/{id}/advice` | Written assessment |
| `POST` | `/api/paste` | Create from pasted listing text |
| `GET` `PUT` | `/api/profiles` | Saved searches |
| `POST` | `/api/profiles/test` | Dry run one listing against every profile |
| `POST` | `/api/ingest/mailbox` | Poll the alert mailbox now |
| `POST` | `/api/geocode` | Place name → LKS-94 coordinates |
| `POST` | `/api/layers/refresh` | Re-download nature layers |
| `GET` | `/api/health` | Liveness |

---

## Layout

```
backend/app/
  config.py           env vars, all 60 municipalities
  db.py               schema, settings store, refresh log, migrations
  geo.py              LKS-94 <-> WGS84, distances, WKT            PURE
  scoring.py          hard flags, weighted score, cost model      PURE
  filters.py          profile presets and matching                PURE
  advisor.py          nature scoring and written assessment       PURE
  notify.py           Telegram push
  api.py              routes
  main.py             app + APScheduler
  sources/
    ntr.py            Registrų centras building-stock collector
    nature.py         lakes, rivers, gazetteer, protected areas
    parsers.py        per-portal alert extraction                 PURE
    mailbox.py        IMAP poller
frontend/
  index.html  styles.css  app.js      no framework, no build step
```

Modules marked PURE do no I/O and are where to add tests first.
