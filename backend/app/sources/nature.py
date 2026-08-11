"""Nature layers: lakes, rivers, settlements, protected areas.

All four come from data.gov.lt (Allow: /). They are downloaded once into local
tables and then queried offline — ~9,300 water features and ~20,900 settlements
fit comfortably in SQLite, and a coarse grid index makes nearest-feature lookup
instant.

Axis order is a trap: the environment agency and Registrų centras write WKT as
(northing, easting) in LKS-94; the protected-areas cadastre writes (lat, lon) in
WGS84. Both are normalised here to LKS-94 (easting, northing).
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

import httpx

from ..config import DATA_GOV_BASE, HTTP_TIMEOUT, HTTP_UA
from ..db import connect, log_refresh
from ..geo import bbox, centroid, dist_m, wgs84_to_lks94, wkt_points

log = logging.getLogger(__name__)

LAKES = "datasets/gov/aaa/ezerai_tvenkiniai/EzerasTvenkinys"
RIVERS = "datasets/gov/aaa/upes_kanalai/UpeKanalas"
PLACES = "datasets/gov/rc/ar/gragyvenamojivietove/GraGyvenamojiVietove"
PROTECTED = "datasets/gov/vstt/teritorijos"

# Coarse grid for nearest-neighbour search: 10 km cells in LKS-94 metres.
CELL = 10_000


def cell_of(e: float, n: float) -> tuple[int, int]:
    return int(e // CELL), int(n // CELL)


async def _fetch(client: httpx.AsyncClient, model: str,
                 select: str | None = None, where: str = "") -> list[dict]:
    """Fetch a Spinta model in one request.

    Cursor pagination is not offered alongside `select()` — `_page.next` comes
    back empty — but an unlimited request returns the whole set. Large models are
    therefore chunked by the caller (see `refresh_places`, which splits by
    municipality) rather than by cursor.
    """
    q = "&".join(x for x in (f"select({select})" if select else "", where) if x)
    url = f"{DATA_GOV_BASE}{model}" + (f"?{q}" if q else "")
    last: Exception | None = None
    for attempt in range(4):
        try:
            r = await client.get(url)
            if r.status_code == 200:
                return r.json().get("_data") or []
            last = RuntimeError(f"HTTP {r.status_code}")
        except Exception as exc:      # transient 500s and truncated bodies
            last = exc
        await asyncio.sleep(2 * (attempt + 1))
    raise RuntimeError(f"{model} [{q[:60]}]: {last}")


GEOM_KEYS = ("geometrija", "shape", "geom", "koord", "gyv_vietoves", "riba")


def _geom_of(row: dict) -> str | None:
    """Find the geometry column. Protected-area models do not agree on a name."""
    for k in GEOM_KEYS:
        v = row.get(k)
        if isinstance(v, str) and ("(" in v):
            return v
    for v in row.values():
        if isinstance(v, str) and v[:12].upper().startswith(
                ("POINT", "POLYGON", "MULTIPOL", "LINESTR", "MULTILIN")):
            return v
    return None


def _lks_from_point(wkt: str | None) -> tuple[float, float] | None:
    """AAA / RC write POINT (northing easting). Return (easting, northing)."""
    pts = wkt_points(wkt or "", limit=1)
    if not pts:
        return None
    north, east = pts[0]
    return east, north


async def refresh_water() -> int:
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[tuple] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT,
                                 headers={"User-Agent": HTTP_UA,
                                          "Accept": "application/json"}) as client:
        for r in await _fetch(client, LAKES, "pavadinimas,koord,pav_plotas"):
            p = _lks_from_point(r.get("koord"))
            if p:
                # `pav_plotas` is surface area in hectares.
                rows.append(("lake", r.get("pavadinimas") or "Bevardis",
                             p[0], p[1], float(r.get("pav_plotas") or 0),
                             *cell_of(*p)))
        for r in await _fetch(client, RIVERS, "pavadinimas,koord,ilgis"):
            p = _lks_from_point(r.get("koord"))
            if p:
                rows.append(("river", r.get("pavadinimas") or "Bevardis",
                             p[0], p[1], float(r.get("ilgis") or 0),
                             *cell_of(*p)))

    with connect() as cx:
        cx.execute("DELETE FROM water_feature")
        cx.executemany(
            "INSERT INTO water_feature(kind,name,easting,northing,size,cell_x,cell_y) "
            "VALUES(?,?,?,?,?,?,?)", rows)
    log_refresh("water", "ok", f"{len(rows)} vandens telkinių", len(rows), started)
    log.info("water layer: %s features", len(rows))
    return len(rows)


async def refresh_places() -> int:
    """Settlement gazetteer — the offline geocoder and radius engine."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    munis: dict[int, str] = {}
    rows: list[tuple] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT,
                                 headers={"User-Agent": HTTP_UA,
                                          "Accept": "application/json"}) as client:
        r = await client.get(
            f"{DATA_GOV_BASE}datasets/gov/rc/ar/savivaldybe/Savivaldybe?limit(100)")
        for m in r.json()["_data"]:
            # Chunk by sav_kodas, not by _id: the settlement dataset's
            # `savivaldybe` reference carries different internal ids than the
            # municipality model hands out, so _id joins silently return zero.
            munis[int(m["sav_kodas"])] = m["pavadinimas"]

        # ~21k settlements with polygon geometry is ~100 MB in one response, so
        # split the request by municipality: 60 chunks of a couple of MB each.
        for code, mname in munis.items():
            try:
                batch = await _fetch(
                    client, PLACES,
                    "gyv_kodas,pavadinimas,plotas,gyv_vietoves",
                    where=f"savivaldybe.sav_kodas={code}")
            except Exception as exc:
                log.warning("gazetteer %s skipped: %s", mname, exc)
                continue
            for row in batch:
                c = centroid(wkt_points(row.get("gyv_vietoves") or "", limit=400))
                if not c:
                    continue
                north, east = c  # written (northing, easting)
                rows.append((int(row["gyv_kodas"]), row.get("pavadinimas") or "",
                             mname, east, north, float(row.get("plotas") or 0),
                             *cell_of(east, north)))
            log.info("gazetteer %s: %s settlements", mname, len(batch))

    with connect() as cx:
        cx.execute("DELETE FROM place")
        cx.executemany(
            "INSERT OR REPLACE INTO place(code,name,municipality,easting,northing,"
            "area_ha,cell_x,cell_y) VALUES(?,?,?,?,?,?,?,?)", rows)
    log_refresh("places", "ok", f"{len(rows)} gyvenviečių", len(rows), started)
    log.info("gazetteer: %s settlements", len(rows))
    return len(rows)


async def refresh_protected() -> int:
    """Protected areas as bounding boxes — enough to say 'verify this one'."""
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: list[tuple] = []
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT,
                                 headers={"User-Agent": HTTP_UA,
                                          "Accept": "application/json"}) as client:
        ns = await client.get(f"{DATA_GOV_BASE}{PROTECTED}/:ns")
        models = [m["name"] for m in (ns.json().get("_data") or [])
                  if not m["name"].endswith(":ns")]
        for model in models:
            kind = model.rsplit("/", 1)[-1]
            try:
                # Field names vary across the 18 models, so take whole rows and
                # locate the geometry column by inspection.
                for r in await _fetch(client, model):
                    pts = wkt_points(_geom_of(r) or "", limit=800)
                    if not pts:
                        continue
                    # cadastre writes (lat, lon)
                    lks = [wgs84_to_lks94(a, b) for a, b in pts]
                    box = bbox(lks)
                    if not box:
                        continue
                    rows.append((kind, r.get("pavadinimas") or kind,
                                 float(r.get("plotas") or 0), *box))
            except Exception as exc:
                log.warning("protected model %s skipped: %s", model, exc)

    with connect() as cx:
        cx.execute("DELETE FROM protected_area")
        cx.executemany(
            "INSERT INTO protected_area(kind,name,area_ha,min_e,min_n,max_e,max_n) "
            "VALUES(?,?,?,?,?,?,?)", rows)
    log_refresh("protected", "ok", f"{len(rows)} saugomų teritorijų", len(rows), started)
    log.info("protected areas: %s", len(rows))
    return len(rows)


# ------------------------------------------------------------------ queries
# The gazetteer stores names in the genitive with a type suffix — "Utenos m.",
# "Kirdeikių k." — so a user typing "Utena" or "Kirdeikiai" matches nothing on a
# plain prefix search. Trimming the inflected ending and matching on the stem
# fixes both directions.
_SUFFIXES = (" k.", " m.", " mstl.", " vs.", " kaimas", " miestas")
_ENDINGS = ("iškės", "iškių", "ių", "ios", "ai", "as", "ys", "is", "us",
            "os", "ės", "ą", "a", "ė", "e", "s", "o", "ų", "u", "i", "y")


def _stems(name: str) -> list[str]:
    """Progressively shorter stems, longest first."""
    base = name.strip()
    for suf in _SUFFIXES:
        if base.lower().endswith(suf):
            base = base[: -len(suf)]
            break
    base = base.strip().rstrip(".").strip()
    out = [base]
    for end in _ENDINGS:
        if base.lower().endswith(end) and len(base) - len(end) >= 3:
            out.append(base[: -len(end)])
    # Keep only stems long enough to be discriminating, longest first.
    return sorted({s for s in out if len(s) >= 3}, key=len, reverse=True)


def geocode(name: str | None, municipality: str | None = None) -> dict[str, Any] | None:
    """Settlement name -> coordinates. Tolerates 'Paberžių k.', 'Paberžiai', 'Utena'.

    Ties break on area: for an ambiguous stem the largest settlement wins, which
    is what someone typing a bare city name means.
    """
    if not name or not name.strip():
        return None
    for stem in _stems(name):
        with connect() as cx:
            if municipality:
                row = cx.execute(
                    "SELECT * FROM place WHERE name LIKE ? AND municipality=? "
                    "ORDER BY area_ha DESC LIMIT 1", [f"{stem}%", municipality]).fetchone()
                if row:
                    return dict(row)
            row = cx.execute("SELECT * FROM place WHERE name LIKE ? "
                             "ORDER BY area_ha DESC LIMIT 1", [f"{stem}%"]).fetchone()
            if row:
                return dict(row)
    return None


def resolve_centre(name: str | None) -> dict[str, Any] | None:
    """A *search centre*: a settlement if the name is one, otherwise a lake.

    geocode() above stays settlement-only on purpose, and this is deliberately
    a second function rather than a flag on it. A listing's `locality` is
    always a settlement; letting that fall back to water would place a
    homestead in the middle of a lake and measure every distance it owns from
    there. A profile *centre* is the opposite kind of name — it says which
    pocket of country to search, and some pockets have no settlement of their
    own. Lūkstas (1001 ha, Varnių regional park) is one: villages are
    scattered round it, the gazetteer holds no "Lūkstas", and naming it as a
    centre resolved to nothing at all.

    Settlements win on purpose where a name is both. "Plateliai" is a village
    and, as "Platelių ežeras", the lake beside it; an operator naming
    Plateliai means the village, which is where the roads and the listings
    are.

    Lakes and reservoirs only — never rivers, though `water_feature` holds
    both. A river is stored as one point taken off a line up to 476 km long,
    so those coordinates are not a place, and a radius drawn round them would
    sit somewhere nobody asked for. A river name therefore stays unresolved,
    and unresolved is loud (filters._radius_misses turns it into a HARD miss)
    rather than quietly wrong.

    Returns the mapping geocode returns, plus `kind`, so callers that only
    want easting/northing consume either without knowing which they got.
    """
    place = geocode(name)
    if place:
        return {**place, "kind": "place"}
    if not name or not name.strip():
        return None
    for stem in _stems(name):
        with connect() as cx:
            row = cx.execute(
                "SELECT name, easting, northing, size FROM water_feature "
                "WHERE kind='lake' AND name LIKE ? ORDER BY size DESC LIMIT 1",
                [f"{stem}%"]).fetchone()
        if row:
            # Largest first: the stem of "Lūkstas" must land on the 1001 ha
            # lake, not on whatever small pond shares its opening letters.
            return {**dict(row), "kind": "lake", "municipality": None}
    return None


def nearest_water(easting: float, northing: float,
                  kind: str, min_size: float = 0.0) -> dict[str, Any] | None:
    """Nearest lake or river, searching outward in 10 km rings."""
    cx0, cy0 = cell_of(easting, northing)
    with connect() as cx:
        for ring in range(0, 6):
            cells = [(cx0 + dx, cy0 + dy)
                     for dx in range(-ring, ring + 1)
                     for dy in range(-ring, ring + 1)
                     if max(abs(dx), abs(dy)) == ring]
            if not cells:
                continue
            ph = ",".join("(?,?)" for _ in cells)
            flat = [v for c in cells for v in c]
            rows = cx.execute(
                f"SELECT * FROM water_feature WHERE kind=? AND size>=? "
                f"AND (cell_x,cell_y) IN ({ph})", [kind, min_size, *flat]).fetchall()
            if rows:
                best = min(rows, key=lambda r: dist_m(easting, northing,
                                                      r["easting"], r["northing"]))
                return {"name": best["name"], "kind": kind, "size": best["size"],
                        "distance_m": round(dist_m(easting, northing,
                                                   best["easting"], best["northing"]))}
    return None


def protected_hits(easting: float, northing: float, pad_m: float = 0.0) -> list[dict]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT kind,name,area_ha FROM protected_area WHERE "
            "? BETWEEN min_e-? AND max_e+? AND ? BETWEEN min_n-? AND max_n+?",
            (easting, pad_m, pad_m, northing, pad_m, pad_m)).fetchall()
    return [dict(r) for r in rows]
