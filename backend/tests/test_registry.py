import pytest

from backend.app.sources import registry as reg


def test_rinka_is_pollable():
    assert reg.is_pollable("rinka")
    assert reg.get("rinka").crawl_delay_s >= 1.0


def test_ntaukcionai_honours_declared_crawl_delay():
    assert reg.get("ntaukcionai").crawl_delay_s == 10.0


# Every declared source that is not POLL, whatever its policy: the gate must
# refuse LINK_ONLY (reCAPTCHA-protected) and MANUAL (ToS-forbidden, or no host
# at all) exactly as firmly as it refuses ALERT_ONLY. Only the alert-only ones
# were listed before, so nothing pinned the other two policies.
@pytest.mark.parametrize("key", ["aruodas", "domoplius", "evarzytynes",
                                 "kampas", "skelbiu", "alio",
                                 "aukcionai_turtas", "facebook", "manual"])
def test_sources_that_are_not_pollable_are_refused(key):
    assert not reg.is_pollable(key)
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable(key)


def test_every_declared_source_is_covered_by_a_policy_the_gate_understands():
    """A source with an unrecognised policy string would sail past
    is_pollable's `== POLL` check as "not pollable" while telling the reader
    nothing. Pin the vocabulary instead."""
    assert {s.policy for s in reg.SOURCES} <= {
        reg.POLL, reg.ALERT_ONLY, reg.LINK_ONLY, reg.MANUAL}


def test_unknown_source_is_refused():
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable("some-portal-we-never-checked")


def test_assert_pollable_returns_the_source():
    assert reg.assert_pollable("rinka").host == "www.rinka.lt"


def test_stale_flags_old_checks_only():
    assert reg.stale("2026-09-01", max_age_days=90) == []
    assert "rinka" in reg.stale("2027-01-01", max_age_days=90)
