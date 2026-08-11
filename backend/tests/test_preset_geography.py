"""The four municipality lists behind PRESETS (filters.py) widened when a
poller was added alongside mailbox ingestion: the mailbox path only ever saw
listings a portal's own saved search had already narrowed by geography, so
the lists could stay narrow too. The poller brings the raw nationwide feed,
so the lists had to widen — each on its own logic (HIGH_UTILITY is measured
and must not drift, the other three are geographic and were widened by
region). These tests guard the two ways that widening can go wrong: a typo
that silently narrows a profile instead of widening it, and a profile that
stops being geographically distinct from the others.
"""
from backend.app import filters as f
from backend.app.config import ALL_MUNICIPALITIES

ALL = set(ALL_MUNICIPALITIES)

LISTS = {
    "HIGH_UTILITY": f.HIGH_UTILITY,
    "LAKE_BELT": f.LAKE_BELT,
    "FOREST_BELT": f.FOREST_BELT,
    "CHEAPEST": f.CHEAPEST,
}


def test_every_municipality_in_every_list_is_a_real_municipality():
    for name, munis in LISTS.items():
        unknown = [m for m in munis if m not in ALL]
        assert not unknown, f"{name} has unknown municipalities: {unknown}"


def test_every_municipality_in_every_preset_is_a_real_municipality():
    for preset in f.PRESETS:
        unknown = [m for m in preset["municipalities"] if m not in ALL]
        assert not unknown, (
            f"preset {preset['key']!r} has unknown municipalities: {unknown}")


def test_high_utility_and_cheapest_do_not_overlap():
    # They encode opposite theses: HIGH_UTILITY is where infrastructure is
    # measurably common, CHEAPEST is where the fund is cheap regardless.
    overlap = set(f.HIGH_UTILITY) & set(f.CHEAPEST)
    assert not overlap, f"HIGH_UTILITY and CHEAPEST both contain: {overlap}"


def test_no_list_contains_duplicates():
    for name, munis in LISTS.items():
        assert len(munis) == len(set(munis)), f"{name} has duplicate entries"


def test_high_utility_is_pinned_to_the_measured_rank_order():
    # This is the one list derived from real data (NTR building register:
    # share with registered power+water x share built pre-1945). The cut is
    # the observed break in the ranking (4.83 down to 3.85), not a round
    # number, and Šalčininkų is deliberately excluded despite being
    # geographically close to several of these. Pinned so a future edit
    # cannot turn "measured" back into "chosen" without this test noticing.
    assert f.HIGH_UTILITY == [
        "Ukmergės rajono", "Utenos rajono", "Anykščių rajono", "Zarasų rajono",
        "Molėtų rajono", "Ignalinos rajono", "Širvintų rajono", "Varėnos rajono",
        "Prienų rajono", "Rokiškio rajono", "Švenčionių rajono", "Trakų rajono",
    ]


def test_the_four_profiles_remain_geographically_distinct():
    # Widening must not collapse the four lists into one undifferentiated
    # "anywhere in Lithuania" set — that would defeat the point of having
    # separate profiles at all.
    as_sets = [frozenset(munis) for munis in LISTS.values()]
    assert len(set(as_sets)) > 1
