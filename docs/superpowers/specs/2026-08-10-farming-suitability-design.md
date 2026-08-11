# Farming suitability — design

Date: 2026-08-10
Status: designed, **build deferred until Plan B lands parcel geometry**
Depends on: `2026-08-10-sodyba-radar-v2-design.md` §3 (location resolution ladder)

---

## 1. What this is, and what it is not

A second score answering a different question from the one Sodyba Radar already
answers. The existing model asks *is this property worth buying*. This asks *can
I farm it*. They are not the same question and must not be averaged into one
number: a cheap house on unfarmable land and a good field with a ruined building
are both interesting, and a merged score hides exactly that tension.

Intended uses, chosen 2026-08-10: **garden and orchard**, **hay / pasture / a few
animals**, **forest, agroforestry and bees**. Commercial cropping is explicitly
excluded — it needs field sizes and machinery access far outside the budget band.
Assumed target: roughly 0.5–5 ha.

**Not in scope, permanently, for this spec:** weather forecasts, storm warnings,
flood nowcasts, drought alerts, crop-yield modelling, irrigation planning. Those
form a *farm operations advisor* — a separate product with a daily refresh
cadence, for land already owned. Recent storms and floods are weather, not
signal, and are close to worthless as inputs to a purchase decision.

---

## 2. Integration: a separate sub-score

`farm_score` is computed alongside `weighted_score` and never merged into it.

New PURE module `backend/app/farming.py`, deliberately mirroring `scoring.py`:
the same 0–10 criterion scale, the same server-side weight normalisation, the
same flags-evaluated-first structure. That symmetry is load-bearing — the
frontend's existing `coreBar()` renders a second stratigraphy column with no new
drawing code, and the weight-slider UI works unchanged.

**Farm flags zero the farm score. They never reject the candidate.** The buying
verdict stays entirely with `scoring.evaluate`. `farming.py` neither imports nor
modifies `scoring.py`.

Rejected alternatives:

- *Extend the existing ten criteria to seventeen.* One ranking, simplest code —
  but it scores farming on candidates you would never farm, moves the reference
  case `AGENT.md` locks at 6.56, and a seventeen-way weight slider stops
  conveying anything.
- *Build on v2 §5 property-type criteria sets.* The principled endpoint, but it
  defers this behind two other plans.

---

## 3. Criteria

Seven, each 0–10, weights normalised server-side exactly as `scoring.py` does.

| Key | Label (LT) | Source | Serves |
|---|---|---|---|
| `terrain` | Reljefas | Copernicus DEM: slope, aspect, cold-air pooling | Orchard frost risk, workability |
| `drainage` | Melioracija | Mel_DR10LT condition | Pasture, any wet ground |
| `soil` | Dirvožemis | Texture + našumo balas | All three uses |
| `farm_water` | Ūkio vanduo | Surface-water distance, borehole plausibility | Stock water, irrigation |
| `woodland` | Miškas | Miškų valstybės kadastras: group, species, age | Forestry, firewood |
| `forage` | Medingumas | Landcover mix within 3 km | Bees, pasture quality |
| `climate` | Klimatas | Open-Meteo CMIP6, 10 km | Growing season, water balance |

### Naming

`farm_water` is deliberately distinct from the existing `water` criterion. That
one scores amenity and beauty — distance to a lake you can look at. This one
scores whether animals can drink and a garden can be watered. They will disagree
on the same property, and that disagreement is information.

### `climate`, and its honest limit

Downscaled CMIP6 is 10 km. Two candidates in the same municipality receive an
identical `climate` score, so this criterion discriminates across regions —
coastal versus eastern Lithuania differ materially in growing-season length and
summer water balance — and not between neighbouring parcels.

It ships with its resolution stated in the interface where the number appears,
the same discipline the nature scores already apply to their ±1 km. Inputs:
growing-season length trend since 1990, frost-free days, and summer
precipitation-minus-evapotranspiration, each as a 30-year normal plus the
2050 projection.

---

## 4. Farm flags

Three. Each zeroes `farm_score` and is reported with its verification link.

| Flag | Test | Why it is binary |
|---|---|---|
| `forest_protective` | Miškų kadastras group I or IA | Felling and most use barred outright |
| `drainage_dead` | Mel_DR10LT condition poor or deregistered | The field is wet and you inherit a shared repair obligation |
| `no_water_access` | No surface water and no plausible borehole | Nothing grazes and nothing grows |

`drainage_dead` deserves emphasis: Mel_DR10LT recorded **2,674,155.98 ha** of
drained land as of 2026-07-01. Most Lithuanian farmland is farmable only because
of Soviet-era subsurface drainage now 40–60 years old. Buying into a failed
system means a wet field *and* a maintenance obligation shared with neighbours.
This is the single most under-appreciated risk in Lithuanian rural land, and no
amount of good scoring elsewhere compensates for it.

---

## 5. Tier gating — the dependency that sets the build order

Drainage, soil and terrain vary field to field. At Tier C — a settlement
centroid, ±1 km — they are district averages wearing a parcel's name. Presenting
them as parcel facts would be precisely the confident-lying failure this project
exists to avoid.

| Location tier | Computable |
|---|---|
| `A_PARCEL` | all seven criteria, all three flags |
| `B_STREET` | `climate`, `forage`; others UNKNOWN |
| `C_PLACE`, `D_MUNI` | `climate` only; everything else UNKNOWN |

`farm_score` is `None` — never a number — unless at least five criteria resolved.
UNKNOWN renders as UNKNOWN, never as zero and never as a midpoint.

**This is why the build is deferred.** Today every candidate resolves at Tier C,
so six of seven criteria would read UNKNOWN and the feature would be an empty
frame. Plan B delivers the parcel geometry that makes it real.

---

## 6. Data sources

Verified 2026-08-10:

| Source | What | Status |
|---|---|---|
| [Open-Meteo Climate API](https://open-meteo.com/en/docs/climate-api) | CMIP6 downscaled to 10 km, daily, 1950–2050, bias-corrected against ERA5. No API key | verified |
| [Mel_DR10LT / Mel_DR2LT](https://data.gov.lt/datasets/3018/) | Drained land and drainage structures, 1:2000, with condition. Held by ŽŪDC | verified |
| [Miškų valstybės kadastras](https://data.gov.lt/datasets/3779/) | Forest cadastre polygons, group, species, age | verified |
| [api.meteo.lt](https://api.meteo.lt/) | LHMT stations, 10 years history, 95 hydrological stations | verified |
| [Copernicus EDO](https://drought.emergency.copernicus.eu/) | Combined Drought Indicator v4.1, soil moisture anomaly | verified |

**Not yet verified — must be confirmed before implementation:**

- Copernicus DEM at 10–30 m for Lithuania: licence and access route unconfirmed.
- Lithuanian soil map / našumo balas as open data: existence and licence
  unconfirmed. ESDAC is the European fallback but is coarser.
- CORINE Land Cover or an OSM-derived alternative for `forage`.

`zudc.lt` is already declared `POLL` in `sources/registry.py` from Plan A, so the
drainage data has a lawful, already-approved access route.

---

## 7. Interfaces

```
backend/app/farming.py                                          PURE  new
  FARM_CRITERIA: list[tuple[str, str, float]]
  FARM_FLAGS:    list[tuple[str, str]]
  assess(candidate, layers) -> FarmAssessment{
      scores:   dict[str, int | None]      # None means UNKNOWN
      flags:    list[str]
      score:    float | None               # None if <5 criteria resolved
      tier:     str                        # copied from the location ladder
      findings: list[Finding]              # each names its source
  }
  normalised_farm_weights(raw) -> dict[str, float]

backend/app/sources/drainage.py    Mel_DR10LT collector              new
backend/app/sources/soil.py        soil layer collector              new
```

`layers` is injected, so `farming.py` does no I/O and is testable against
fixtures — the same pattern `scoring.py` follows.

Schema: `candidate.farm_json TEXT NOT NULL DEFAULT '{}'`, added through
`db.MIGRATIONS` as an additive column.

API: `/api/schema` gains `farm_criteria` and `farm_flags` so the UI renders
itself from the server, as it already does for the buying criteria.
`/api/candidates` gains `min_farm_score` and a `eur_per_farm_point` sort.

---

## 8. Frontend

A second core-sample column headed **Ūkis** beside the existing one, using the
existing `coreBar()`. Farm flags render as chips. The climate context — trend
since 1990 and the 2050 projection for the district — lives in the drawer, not
the table, because it is identical for every candidate in a municipality and
would be noise in a sortable column.

New sort: cheapest per farming point.

---

## 9. Testing

`farming.py` is PURE with layers injected.

- Each criterion's banding at its boundaries.
- Each flag zeroes `farm_score` while leaving the buying `verdict` untouched —
  the separation in §2 is the property most worth protecting.
- Tier gating: Tier C input yields `climate` only and `farm_score is None`.
- Fewer than five resolved criteria yields `None`, not a partial average.
- A candidate with `drainage_dead` still reaches `shortlist` on the buying score
  if its buying criteria warrant it.

---

## 10. Open questions

1. Weights across the seven criteria are unset. They should start from the three
   chosen uses rather than from intuition, and the choice is easier once real
   parcels are scored — defer to implementation.
2. Whether `forage` earns its place. It serves bees alone among the three uses
   and needs a landcover layer nothing else uses. Cut it if the layer proves
   awkward.
3. Whether `drainage_dead` should be a flag for *all* uses. It is decisive for
   pasture, largely irrelevant for a kitchen garden or woodland. Possibly it
   should be a flag only when plot size suggests grazing, which would make it the
   first use-conditional flag in the system.
