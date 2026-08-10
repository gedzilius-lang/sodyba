import pytest

from backend.app.sources import registry as reg


def test_rinka_is_pollable():
    assert reg.is_pollable("rinka")
    assert reg.get("rinka").crawl_delay_s >= 1.0


def test_ntaukcionai_honours_declared_crawl_delay():
    assert reg.get("ntaukcionai").crawl_delay_s == 10.0


@pytest.mark.parametrize("key", ["aruodas", "domoplius", "evarzytynes",
                                 "kampas", "skelbiu", "alio"])
def test_alert_only_sources_are_not_pollable(key):
    assert not reg.is_pollable(key)
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable(key)


def test_unknown_source_is_refused():
    with pytest.raises(reg.PolicyError):
        reg.assert_pollable("some-portal-we-never-checked")


def test_assert_pollable_returns_the_source():
    assert reg.assert_pollable("rinka").host == "www.rinka.lt"


def test_stale_flags_old_checks_only():
    assert reg.stale("2026-09-01", max_age_days=90) == []
    assert "rinka" in reg.stale("2027-01-01", max_age_days=90)
