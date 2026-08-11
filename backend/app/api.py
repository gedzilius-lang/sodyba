"""HTTP API. All state changes go through here; the frontend holds nothing."""
from __future__ import annotations
import json
from datetime import date
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from .advisor import advise, assess_nature
from .config import ALL_MUNICIPALITIES, DEFAULT_MUNICIPALITIES
from .db import connect, get_setting, set_setting
from .scoring import CRITERIA, HARD_FLAGS, COST_LINES, DEFAULT_SETTINGS, evaluate
from .filters import PRESETS, sanitise, match_all, matches, evaluate_all, MATCH, NEAR
from .sources import (refresh_market_stock, poll_mailbox, mailbox_configured,
                      refresh_water, refresh_places, refresh_protected, geocode,
                      poll_all, POLLED, registry)
from . import notify

router = APIRouter(prefix="/api")

SETTINGS_KEY = "scoring"
WATCHLIST_KEY = "municipalities"
PROFILES_KEY = "filter_profiles"


def profiles() -> list[dict[str, Any]]:
    return get_setting(PROFILES_KEY) or PRESETS


def _layer_status() -> dict[str, int]:
    with connect() as cx:
        return {t: cx.execute(f"SELECT COUNT(*) n FROM {t}").fetchone()["n"]
                for t in ("water_feature", "place", "protected_area", "market_stock")}


def settings() -> dict[str, Any]:
    stored = get_setting(SETTINGS_KEY) or {}
    merged = {**DEFAULT_SETTINGS, **stored}
    merged["weights"] = {**DEFAULT_SETTINGS["weights"], **(stored.get("weights") or {})}
    merged["auto_costs"] = {**DEFAULT_SETTINGS["auto_costs"], **(stored.get("auto_costs") or {})}
    return merged


def _row_to_candidate(r) -> dict[str, Any]:
    c = {
        "id": r["id"], "ref": r["ref"], "source": r["source"], "url": r["url"],
        "title": r["title"], "municipality": r["municipality"], "locality": r["locality"],
        "cadastral_no": r["cadastral_no"], "price_eur": r["price_eur"],
        "house_m2": r["house_m2"], "plot_ares": r["plot_ares"],
        "auction_ends_at": r["auction_ends_at"], "notes": r["notes"],
        "archived": bool(r["archived"]), "updated_at": r["updated_at"],
        "profiles": json.loads(r["profiles_json"] or "[]"),
        "easting": r["easting"], "northing": r["northing"],
        "nature": json.loads(r["nature_json"] or "{}"),
        "match_state": r["match_state"],
        "misses": json.loads(r["misses_json"] or "{}"),
        "flags": json.loads(r["flags_json"]), "scores": json.loads(r["scores_json"]),
        "costs": json.loads(r["costs_json"]), "checks": json.loads(r["checks_json"]),
    }
    c.update(evaluate(c, settings()))
    return c


# ----------------------------------------------------------------- schema
@router.get("/schema")
def schema() -> dict[str, Any]:
    """Everything the UI needs to render itself. One call, no hardcoded lists."""
    return {
        "criteria": [{"key": k, "label": l, "default_weight": w} for k, l, w in CRITERIA],
        "hard_flags": [{"key": k, "label": l} for k, l in HARD_FLAGS],
        "cost_lines": [{"key": k, "label": l} for k, l in COST_LINES],
        "municipalities": get_setting(WATCHLIST_KEY) or DEFAULT_MUNICIPALITIES,
        "all_municipalities": ALL_MUNICIPALITIES,
        "settings": settings(),
        "profiles": profiles(),
        "presets": PRESETS,
        "ingest": {
            "mailbox_configured": mailbox_configured(),
            "telegram_configured": notify.enabled(),
            "layers": _layer_status(),
            "polled": POLLED,
            "sources": [
                {"key": s.key, "host": s.host, "policy": s.policy,
                 "robots": s.robots, "checked_at": s.checked_at}
                for s in registry.SOURCES
            ],
            "stale_sources": registry.stale(date.today().isoformat()),
        },
        "checks": [
            {"key": "ntr_extract", "label": "NTR išrašas", "url": "https://www.registrucentras.lt", "cost": "~5 EUR"},
            {"key": "protected_area", "label": "Saugomos teritorijos", "url": "https://vstt.lrv.lt", "cost": "0"},
            {"key": "power", "label": "ESO prijungimas", "url": "https://www.eso.lt", "cost": "0"},
            {"key": "gis", "label": "Savivaldybės GIS", "url": "https://www.regia.lt", "cost": "0"},
            {"key": "forest", "label": "Miškų kadastras", "url": "https://kadastras.amvmt.lt", "cost": "0"},
            {"key": "planning", "label": "Planavimo dokumentai", "url": "https://www.tpdris.lt", "cost": "0"},
            {"key": "valuation", "label": "Masinis vertinimas", "url": "https://masvertinimas.registrucentras.lt", "cost": "0"},
            {"key": "heritage", "label": "Kultūros paveldas", "url": "https://kvr.kpd.lt", "cost": "0"},
            {"key": "access", "label": "Privažiavimas", "url": "https://www.regia.lt", "cost": "0"},
            {"key": "occupants", "label": "Deklaruoti gyventojai", "url": "", "cost": "0"},
            {"key": "terrain", "label": "Reljefas / potvyniai", "url": "https://www.geoportal.lt", "cost": "0"},
            {"key": "site_visit", "label": "Apžiūra vietoje", "url": "", "cost": "~250 EUR"},
        ],
    }


# ----------------------------------------------------------------- settings
class SettingsIn(BaseModel):
    weights: Optional[dict[str, float]] = None
    budget_ceiling_eur: Optional[float] = Field(None, ge=0)
    min_score: Optional[float] = Field(None, ge=0, le=10)
    contingency_pct: Optional[float] = Field(None, ge=0, le=2)
    auto_costs: Optional[dict[str, float]] = None
    municipalities: Optional[list[str]] = None


@router.put("/settings")
def put_settings(body: SettingsIn) -> dict[str, Any]:
    cur = settings()
    patch = body.model_dump(exclude_none=True)
    if "municipalities" in patch:
        set_setting(WATCHLIST_KEY, patch.pop("municipalities"))
    if "weights" in patch:
        cur["weights"] = {**cur["weights"], **patch.pop("weights")}
    if "auto_costs" in patch:
        cur["auto_costs"] = {**cur["auto_costs"], **patch.pop("auto_costs")}
    cur.update(patch)
    set_setting(SETTINGS_KEY, cur)
    return settings()


# ----------------------------------------------------------------- candidates
class CandidateIn(BaseModel):
    ref: Optional[str] = None
    source: str = "manual"
    url: Optional[str] = None
    title: Optional[str] = None
    municipality: Optional[str] = None
    locality: Optional[str] = None
    cadastral_no: Optional[str] = None
    price_eur: Optional[float] = Field(None, ge=0)
    house_m2: Optional[float] = Field(None, ge=0)
    plot_ares: Optional[float] = Field(None, ge=0)
    auction_ends_at: Optional[str] = None
    flags: dict[str, bool] = {}
    scores: dict[str, float] = {}
    costs: dict[str, float] = {}
    checks: dict[str, bool] = {}
    notes: Optional[str] = None
    archived: bool = False


def _next_ref() -> str:
    with connect() as cx:
        n = cx.execute("SELECT COUNT(*) c FROM candidate").fetchone()["c"]
    return f"K{n + 1:03d}"


@router.get("/candidates")
def list_candidates(
    municipality: Optional[str] = None,
    verdict: Optional[str] = None,
    max_price: Optional[float] = None,
    min_price: Optional[float] = None,
    min_score: Optional[float] = None,
    max_total_cost: Optional[float] = None,
    source: Optional[str] = None,
    match_state: str = "match",
    profile: Optional[str] = None,
    near: Optional[str] = None,
    radius_km: Optional[float] = None,
    max_lake_m: Optional[float] = None,
    max_river_m: Optional[float] = None,
    include_archived: bool = False,
    q: Optional[str] = None,
    sort: str = Query("eur_per_point", pattern=r"^[a-z_]+$"),
) -> dict[str, Any]:
    sql = "SELECT * FROM candidate WHERE 1=1"
    args: list[Any] = []
    if not include_archived:
        sql += " AND archived=0"
    if municipality:
        sql += " AND municipality=?"; args.append(municipality)
    if source:
        sql += " AND source=?"; args.append(source)
    if match_state != "all":
        sql += " AND match_state=?"; args.append(match_state)
    if min_price is not None:
        sql += " AND price_eur >= ?"; args.append(min_price)
    if max_price is not None:
        sql += " AND price_eur <= ?"; args.append(max_price)
    if q:
        sql += " AND (title LIKE ? OR locality LIKE ? OR notes LIKE ?)"
        args += [f"%{q}%"] * 3

    with connect() as cx:
        rows = cx.execute(sql, args).fetchall()

    items = [_row_to_candidate(r) for r in rows]
    if profile:
        items = [c for c in items if profile in (c.get("profiles") or [])]
    if near and radius_km:
        from .geo import dist_m
        centre = geocode(near)
        if not centre:
            # Fail loudly: silently ignoring the filter would show the user
            # nationwide results while the radius box still reads "40 km".
            raise HTTPException(404, f"vietovė nerasta: {near}")

        def within(c):
            e, n = c.get("easting"), c.get("northing")
            if e is None or n is None:
                return False
            return dist_m(e, n, centre["easting"], centre["northing"]) <= radius_km * 1000

        items = [c for c in items if within(c)]
    if max_lake_m is not None:
        items = [c for c in items
                 if (c.get("nature") or {}).get("nearest_lake")
                 and c["nature"]["nearest_lake"]["distance_m"] <= max_lake_m]
    if max_river_m is not None:
        items = [c for c in items
                 if (c.get("nature") or {}).get("nearest_river")
                 and c["nature"]["nearest_river"]["distance_m"] <= max_river_m]
    if verdict:
        items = [c for c in items if c["verdict"] == verdict]
    if min_score is not None:
        items = [c for c in items if (c["weighted_score"] or 0) >= min_score]
    if max_total_cost is not None:
        items = [c for c in items if c["total_cost"] is not None and c["total_cost"] <= max_total_cost]

    rank = {"shortlist": 0, "weak": 1, "over_budget": 2, "incomplete": 3, "rejected": 4}
    if sort == "eur_per_point":
        items.sort(key=lambda c: (rank[c["verdict"]], c["eur_per_point"] if c["eur_per_point"] is not None else 1e12))
    elif sort == "score":
        items.sort(key=lambda c: -(c["weighted_score"] or -1))
    elif sort == "price":
        items.sort(key=lambda c: c["price_eur"] if c["price_eur"] is not None else 1e12)
    elif sort == "total_cost":
        items.sort(key=lambda c: c["total_cost"] if c["total_cost"] is not None else 1e12)
    else:
        items.sort(key=lambda c: c["updated_at"], reverse=True)

    counts: dict[str, int] = {}
    for c in items:
        counts[c["verdict"]] = counts.get(c["verdict"], 0) + 1
    return {"items": items, "count": len(items), "by_verdict": counts}


@router.post("/candidates", status_code=201)
def create_candidate(body: CandidateIn) -> dict[str, Any]:
    ref = body.ref or _next_ref()
    costs = {**settings()["auto_costs"], **body.costs}
    if body.price_eur is not None and "purchase" not in body.costs:
        costs["purchase"] = body.price_eur
    with connect() as cx:
        if cx.execute("SELECT 1 FROM candidate WHERE ref=?", (ref,)).fetchone():
            raise HTTPException(409, f"ref {ref} already exists")
        cur = cx.execute(
            "INSERT INTO candidate(ref,source,url,title,municipality,locality,cadastral_no,"
            "price_eur,house_m2,plot_ares,auction_ends_at,flags_json,scores_json,costs_json,"
            "checks_json,notes,archived) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ref, body.source, body.url, body.title, body.municipality, body.locality,
             body.cadastral_no, body.price_eur, body.house_m2, body.plot_ares,
             body.auction_ends_at, json.dumps(body.flags), json.dumps(body.scores),
             json.dumps(costs), json.dumps(body.checks), body.notes, int(body.archived)),
        )
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cur.lastrowid,)).fetchone()
    return _row_to_candidate(row)


@router.patch("/candidates/{cid}")
def update_candidate(cid: int, body: CandidateIn) -> dict[str, Any]:
    with connect() as cx:
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone()
        if not row:
            raise HTTPException(404, "candidate not found")
        patch = body.model_dump(exclude_unset=True)
        cols = {
            "url": row["url"], "title": row["title"], "municipality": row["municipality"],
            "locality": row["locality"], "cadastral_no": row["cadastral_no"],
            "price_eur": row["price_eur"], "house_m2": row["house_m2"],
            "plot_ares": row["plot_ares"], "auction_ends_at": row["auction_ends_at"],
            "notes": row["notes"], "source": row["source"],
        }
        for k in cols:
            if k in patch:
                cols[k] = patch[k]
        merged = {}
        for jkey, col in (("flags", "flags_json"), ("scores", "scores_json"),
                          ("costs", "costs_json"), ("checks", "checks_json")):
            merged[col] = json.dumps({**json.loads(row[col]), **patch.get(jkey, {})},
                                     ensure_ascii=False)
        archived = int(patch.get("archived", bool(row["archived"])))
        cx.execute(
            "UPDATE candidate SET source=?,url=?,title=?,municipality=?,locality=?,"
            "cadastral_no=?,price_eur=?,house_m2=?,plot_ares=?,auction_ends_at=?,"
            "flags_json=?,scores_json=?,costs_json=?,checks_json=?,notes=?,archived=?,"
            "updated_at=datetime('now') WHERE id=?",
            (cols["source"], cols["url"], cols["title"], cols["municipality"],
             cols["locality"], cols["cadastral_no"], cols["price_eur"], cols["house_m2"],
             cols["plot_ares"], cols["auction_ends_at"], merged["flags_json"],
             merged["scores_json"], merged["costs_json"], merged["checks_json"],
             cols["notes"], archived, cid),
        )
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone()
    return _row_to_candidate(row)


@router.delete("/candidates/{cid}", status_code=204)
def delete_candidate(cid: int):
    with connect() as cx:
        cx.execute("DELETE FROM candidate WHERE id=?", (cid,))


# --------------------------------------------------------------- re-evaluate
class ReevaluateIn(BaseModel):
    dry_run: bool = False


def _reeval_listing(row: dict[str, Any]) -> dict[str, Any]:
    """The same shape ingest hands to filters.evaluate(), rebuilt from a stored row.

    evaluate() reads `listing.get("notes") or listing.get("raw")` for the keyword
    haystack (see filters.py's hay_full comment); ingest passes the pre-storage
    `raw` chunk because at that point nothing has been written yet, but a stored
    row has no `raw` — its description lives in the `notes` column, which is
    exactly the field evaluate() falls back to. So handing it `notes` here gives
    re-evaluation the same view of the text a fresh ingest would have had.
    """
    return {
        "title": row["title"], "locality": row["locality"],
        "municipality": row["municipality"], "price_eur": row["price_eur"],
        "house_m2": row["house_m2"], "plot_ares": row["plot_ares"],
        "source": row["source"], "easting": row["easting"],
        "northing": row["northing"],
        "nature": json.loads(row["nature_json"] or "{}"),
        "notes": row["notes"],
    }


@router.post("/candidates/reevaluate")
def reevaluate_candidates(body: ReevaluateIn = ReevaluateIn()) -> dict[str, Any]:
    """Re-run the current filter profiles over every stored, non-archived row.

    Profiles are otherwise evaluated exactly once, at ingest (mailbox._insert,
    poller.poll_source write match_state/profiles_json/misses_json and nothing
    ever recomputes them). Widen a municipality list or add a keyword and every
    row already in the table keeps the verdict it got under the old rules —
    re-polling does not help either, since _insert short-circuits on
    `fingerprint` before evaluation even runs for a listing already stored.
    This endpoint is the only path that goes back and re-scores what is
    already there against the profiles as they stand today.

    Only match_state, profiles_json, misses_json (and updated_at) ever move.
    scores_json, flags_json, costs_json, checks_json, notes, archived,
    nature_json, easting/northing, ref, url, price_eur and everything else are
    left byte-for-byte as stored — this function does not even read
    scores_json or checks_json. A score set after visiting a place is never
    overwritten by a machine; see api.locate's same rule for derived scores.

    Deliberately does NOT call notify.push. The mailbox and poller paths push
    because they surface something the user has never seen; this path only
    changes the verdict on rows already sitting in the table. Re-evaluating
    fifty candidates after a profile edit must not fire fifty Telegram
    messages — that would turn a filter tweak into a spam event.

    Archived rows are skipped entirely (excluded at the SQL level, not merely
    filtered from the report): archiving is a decision the user already made
    about that listing, and a profile edit resurrecting it into the match tier
    would silently overturn that decision. There is no opt-in to include them
    here — if that turns out to be the wrong call for some workflow, it should
    be a deliberate, visible choice (e.g. an `include_archived` flag), not a
    silent default.
    """
    profs = [p for p in profiles() if p.get("enabled", True)]

    with connect() as cx:
        rows = [dict(r) for r in
                cx.execute("SELECT * FROM candidate WHERE archived=0").fetchall()]

    transitions: list[dict[str, Any]] = []
    to_write: list[tuple[str, str, str, int]] = []

    for row in rows:
        results = evaluate_all(_reeval_listing(row), profs)
        hits = [r.key for r in results if r.state == MATCH]
        nears = [r.key for r in results if r.state == NEAR]
        new_state = "match" if hits else ("near" if nears else "reject")
        new_profiles = hits or nears
        new_misses = {r.key: [vars(m) for m in r.misses]
                      for r in results if r.state in (MATCH, NEAR)}

        old_state = row["match_state"]
        old_profiles = json.loads(row["profiles_json"] or "[]")
        old_misses = json.loads(row["misses_json"] or "{}")
        if (new_state, new_profiles, new_misses) == (old_state, old_profiles, old_misses):
            continue

        transitions.append({"ref": row["ref"], "from": old_state, "to": new_state})
        to_write.append((new_state, json.dumps(new_profiles, ensure_ascii=False),
                         json.dumps(new_misses, ensure_ascii=False), row["id"]))

    if not body.dry_run and to_write:
        with connect() as cx:
            for state, profiles_json, misses_json, cid in to_write:
                cx.execute(
                    "UPDATE candidate SET match_state=?, profiles_json=?, "
                    "misses_json=?, updated_at=datetime('now') WHERE id=?",
                    (state, profiles_json, misses_json, cid))

    return {
        "dry_run": body.dry_run,
        "examined": len(rows),
        "changed": len(transitions),
        "transitions": transitions,
    }


# ------------------------------------------------- paste ingestion (auctions)
class PasteIn(BaseModel):
    text: str
    source: str = "evarzytynes"
    url: Optional[str] = None


@router.post("/paste", status_code=201)
def paste(body: PasteIn) -> dict[str, Any]:
    """Ingest a listing pasted from a portal we may not crawl.

    evarzytynes.lt (Disallow: /) and the classifieds actively block bots, so the
    lawful path is: subscribe to their own email alerts, paste the text here.
    Anything the parser misses you fix in the detail panel.

    Numeric and location fields go through parsers.route() rather than a local
    copy of its regexes, so a fix to the parser (e.g. decimal-tail prices,
    NBSP thousands separators) applies here too instead of silently diverging.

    The declared source wins over sniffing the text. route() picks its parser
    by looking for a portal domain in the body, so pasting an auction notice
    without its URL fell through to the generic parser and reported the market
    valuation where the auction parser reads the starting price — 40000 rather
    than 25000 on the same property, decided by whether the user happened to
    copy the link. Sniffing stays as the fallback for "manual", "facebook" and
    anything else with no portal format of its own.
    """
    t = body.text.strip()
    if not t:
        raise HTTPException(400, "empty text")

    from .sources import parsers
    fn = parsers.parser_for(body.source)
    parsed = fn(parsers.to_text(body.text)) if fn else parsers.route("", "", body.text)

    return create_candidate(CandidateIn(
        source=body.source,
        url=body.url,
        title=t.splitlines()[0][:200] if t.splitlines() else None,
        municipality=parsed.get("municipality"),
        locality=parsed.get("locality"),
        cadastral_no=parsed.get("cadastral_no"),
        price_eur=parsed.get("price_eur"),
        house_m2=parsed.get("house_m2"),
        plot_ares=parsed.get("plot_ares"),
        notes=t[:4000],
    ))


# ----------------------------------------------------------------- market
@router.get("/market")
def market() -> dict[str, Any]:
    with connect() as cx:
        rows = cx.execute("SELECT * FROM market_stock").fetchall()
        last = cx.execute(
            "SELECT * FROM refresh_log ORDER BY id DESC LIMIT 1").fetchone()
    out = []
    for r in rows:
        total = r["total"] or 0
        pw = (r["power_and_water"] or 0) / total if total else 0
        old = (r["pre_1945"] or 0) / total if total else 0
        out.append({
            "municipality": r["municipality"], "total": total,
            "with_power": r["with_power"], "with_water": r["with_water"],
            "power_and_water": r["power_and_water"], "pre_1945": r["pre_1945"],
            "log_walls": r["log_walls"],
            "pct_power": round((r["with_power"] or 0) / total, 4) if total else None,
            "pct_power_water": round(pw, 4),
            "pct_pre_1945": round(old, 4),
            "rarity_index": round(pw * old * 100, 2),
            "fetched_at": r["fetched_at"],
        })
    out.sort(key=lambda x: -x["rarity_index"])
    return {
        "items": out,
        "last_refresh": dict(last) if last else None,
        "note": ("Registrų centras nepublikuoja sandorių kainų kaip atvirų duomenų. "
                 "Tai registruotas fondas, ne pardavimai."),
    }


@router.post("/refresh")
async def refresh() -> dict[str, Any]:
    munis = get_setting(WATCHLIST_KEY) or DEFAULT_MUNICIPALITIES
    n = await refresh_market_stock(munis)
    return {"status": "ok", "municipalities": n}


# ----------------------------------------------------------------- profiles
class ProfilesIn(BaseModel):
    profiles: list[dict[str, Any]]


@router.get("/profiles")
def get_profiles() -> dict[str, Any]:
    return {"profiles": profiles(), "presets": PRESETS}


@router.put("/profiles")
def put_profiles(body: ProfilesIn) -> dict[str, Any]:
    cleaned, seen = [], set()
    for raw in body.profiles:
        try:
            p = sanitise(raw)
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if p["key"] in seen:
            raise HTTPException(400, f"pasikartojantis profilio raktas: {p['key']}")
        seen.add(p["key"])
        cleaned.append(p)
    set_setting(PROFILES_KEY, cleaned)
    return {"profiles": cleaned}


@router.post("/profiles/reset")
def reset_profiles() -> dict[str, Any]:
    set_setting(PROFILES_KEY, PRESETS)
    return {"profiles": PRESETS}


class TestIn(BaseModel):
    text: str


@router.post("/profiles/test")
def test_profiles(body: TestIn) -> dict[str, Any]:
    """Dry run: show which profiles a pasted listing would hit, and why not."""
    from .sources import parsers
    listing = parsers.route("", "", body.text)
    listing["raw"] = body.text
    listing["nature"] = assess_nature(listing)
    out = []
    for p in profiles():
        ok, why = matches(listing, p)
        out.append({"key": p["key"], "name": p["name"], "matched": ok, "reason": why})
    return {"parsed": listing, "results": out}


# ------------------------------------------------------------ nature/advice
def _persist_nature(cid: int, nature: dict[str, Any]) -> None:
    with connect() as cx:
        cx.execute("UPDATE candidate SET nature_json=?, easting=COALESCE(easting,?), "
                   "northing=COALESCE(northing,?), updated_at=datetime('now') WHERE id=?",
                   (json.dumps(nature, ensure_ascii=False), nature.get("easting"),
                    nature.get("northing"), cid))


@router.post("/candidates/{cid}/locate")
def locate(cid: int) -> dict[str, Any]:
    """Geocode, measure water distances, test protected-area envelopes."""
    with connect() as cx:
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "candidate not found")
    c = _row_to_candidate(row)
    nature = assess_nature(c)
    _persist_nature(cid, nature)
    if nature.get("located"):
        derived = nature["derived_scores"]
        with connect() as cx:
            cur = json.loads(cx.execute("SELECT scores_json FROM candidate WHERE id=?",
                                        (cid,)).fetchone()["scores_json"])
            # Measured values win over placeholders, but never overwrite a
            # score you set by hand after seeing the place.
            for k, v in derived.items():
                cur.setdefault(k, v)
            cx.execute("UPDATE candidate SET scores_json=? WHERE id=?",
                       (json.dumps(cur), cid))
    with connect() as cx:
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone()
    return _row_to_candidate(row)


@router.get("/candidates/{cid}/advice")
def advice(cid: int) -> dict[str, Any]:
    with connect() as cx:
        row = cx.execute("SELECT * FROM candidate WHERE id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "candidate not found")
    c = _row_to_candidate(row)
    return {"candidate": {"ref": c["ref"], "verdict": c["verdict"]},
            **advise(c, settings(), market()["items"])}


class GeocodeIn(BaseModel):
    name: str
    municipality: Optional[str] = None


@router.post("/geocode")
def geocode_one(body: GeocodeIn) -> dict[str, Any]:
    p = geocode(body.name, body.municipality)
    if not p:
        raise HTTPException(404, f"vietovė nerasta: {body.name}")
    return p


@router.post("/layers/refresh")
async def refresh_layers() -> dict[str, Any]:
    """Download the nature layers. Slow (~3-6 min); run once, then rarely."""
    water = await refresh_water()
    places = await refresh_places()
    protected = await refresh_protected()
    return {"water_features": water, "places": places, "protected_areas": protected}


# ----------------------------------------------------------------- mailbox
@router.post("/ingest/mailbox")
async def ingest_mailbox() -> dict[str, Any]:
    result = await poll_mailbox()
    if result.get("created"):
        await notify.push(result["created"])
    return result


@router.post("/ingest/poll")
async def ingest_poll() -> dict[str, Any]:
    """Poll every permitted source now. Sources are gated by sources/registry.py."""
    result = await poll_all()
    created = [c for r in result.values() for c in (r.get("created") or [])]
    if created:
        await notify.push(created)
    return result


@router.get("/ingest/log")
def ingest_log(limit: int = 25) -> dict[str, Any]:
    with connect() as cx:
        rows = cx.execute(
            "SELECT * FROM refresh_log ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return {"items": [dict(r) for r in rows]}


@router.get("/health")
def health() -> dict[str, Any]:
    with connect() as cx:
        c = cx.execute("SELECT COUNT(*) n FROM candidate").fetchone()["n"]
        m = cx.execute("SELECT COUNT(*) n FROM market_stock").fetchone()["n"]
    return {"status": "ok", "candidates": c, "market_rows": m}
