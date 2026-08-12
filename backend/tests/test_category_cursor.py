"""Each category carries its own high-water mark."""
from backend.app.db import connect, init_db
from backend.app.sources import poller


def test_absent_cursor_reads_as_zero():
    assert poller._cursor("rinka", "namai_absent_test") == 0


def test_each_category_advances_independently():
    poller._save_cursor("rinka", "cat_a", 5_000_000)
    poller._save_cursor("rinka", "cat_b", 4_000_000)
    assert poller._cursor("rinka", "cat_a") == 5_000_000
    # The lower stream must NOT be dragged up by the higher one. This is the
    # whole reason the table exists.
    assert poller._cursor("rinka", "cat_b") == 4_000_000


def test_saving_twice_updates_rather_than_duplicating():
    poller._save_cursor("rinka", "cat_c", 1)
    poller._save_cursor("rinka", "cat_c", 2)
    assert poller._cursor("rinka", "cat_c") == 2
    with connect() as cx:
        n = cx.execute(
            "SELECT COUNT(*) n FROM source_category_cursor "
            "WHERE source='rinka' AND category='cat_c'").fetchone()["n"]
    assert n == 1


def test_a_malformed_stored_value_reads_as_zero():
    with connect() as cx:
        cx.execute(
            "INSERT INTO source_category_cursor(source,category,last_id) "
            "VALUES('rinka','cat_junk','not-a-number') "
            "ON CONFLICT(source,category) DO UPDATE SET last_id=excluded.last_id")
    assert poller._cursor("rinka", "cat_junk") == 0


def test_sodybos_is_seeded_from_the_old_single_cursor():
    # A database that has been polling since before categories existed must
    # resume where it left off, not re-walk the whole category.
    with connect() as cx:
        cx.execute("DELETE FROM source_category_cursor "
                   "WHERE source='rinka' AND category='sodybos'")
        cx.execute("INSERT INTO source_cursor(source,last_id) VALUES('rinka','4991510') "
                   "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id")
    init_db()          # re-runs SCHEMA, which carries the seed statement
    assert poller._cursor("rinka", "sodybos") == 4991510


def test_only_rinkas_cursor_is_labelled_sodybos():
    """rinka's single cursor was built by walking parduodamos-sodybos, so its
    category is known. No other source's is, and inventing one would plant a
    high watermark on a category that source never polled — suppressing
    exactly the listings this table exists to stop losing."""
    with connect() as cx:
        cx.execute("DELETE FROM source_category_cursor WHERE source='othersrc'")
        cx.execute("INSERT INTO source_cursor(source,last_id) VALUES('othersrc','4991510') "
                   "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id")
    init_db()
    assert poller._cursor("othersrc", "sodybos") == 0
    with connect() as cx:
        n = cx.execute("SELECT COUNT(*) n FROM source_category_cursor "
                       "WHERE source='othersrc'").fetchone()["n"]
    assert n == 0, "the seed guessed a category it could not know"


def test_seeding_is_idempotent_and_does_not_clobber_progress():
    with connect() as cx:
        cx.execute("INSERT INTO source_cursor(source,last_id) VALUES('rinka','100') "
                   "ON CONFLICT(source) DO UPDATE SET last_id=excluded.last_id")
    init_db()
    poller._save_cursor("rinka", "sodybos", 999)
    init_db()          # a later boot must not reset it back to 100
    assert poller._cursor("rinka", "sodybos") == 999
