"""rinka.lt serves several categories from one host under one robots policy."""
import pytest

from backend.app.sources.adapters import rinka


def test_both_categories_are_declared():
    assert set(rinka.CATEGORIES) == {"sodybos", "namai"}


def test_sodybos_path_is_unchanged():
    # The path this project has polled since day one. Changing it would
    # orphan the existing cursor and re-walk 86 listings.
    assert rinka.CATEGORIES["sodybos"] == (
        "/nekilnojamojo-turto-skelbimai/parduodamos-sodybos")


def test_namai_path():
    assert rinka.CATEGORIES["namai"] == (
        "/nekilnojamojo-turto-skelbimai/parduodami-namai")


def test_list_url_builds_the_category_path():
    assert rinka.list_url("namai", page=2, per_page=200) == (
        "https://www.rinka.lt/nekilnojamojo-turto-skelbimai/"
        "parduodami-namai?page=2&per_page=200")


def test_list_url_defaults_to_page_one():
    assert rinka.list_url("sodybos").endswith("?page=1&per_page=200")


def test_unknown_category_raises_rather_than_falling_back():
    # Silently defaulting to sodybos would make a typo look like a working
    # poll that quietly searched the wrong category.
    with pytest.raises(rinka.UnknownCategory):
        rinka.list_url("butai")
