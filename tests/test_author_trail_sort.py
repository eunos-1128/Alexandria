"""Sorting the author trail — asked for 2026-09-10.

The sidebar had exactly one order: `position`, which starts as
add-order and is rewritten by every drag. So there was no way to find
a name alphabetically in a list of 27, and no way to ask who you
started following first, because after the first drag `position` no
longer answers that.

Four orders now: Custom (the drag arrangement, still the default),
Surname, Date added, First publication. The interesting cases are the
ones where two of them disagree.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import index


@pytest.fixture
def conn(tmp_path):
    return index.open_db(str(tmp_path / "lib" / "library.db"))


def add(conn, key, name, added_at=None, first_year=None):
    index.add_author_trail(conn, {"openalex_id": key, "name": name})
    if added_at:
        conn.execute("UPDATE authors.author_trail SET added_at = ? "
                     "WHERE key = ?", (added_at, key))
        conn.commit()
    if first_year:
        index.set_author_first_publication_year(conn, key, first_year)


def names(conn, order):
    return [r["name"] for r in index.list_author_trail(conn, order)]


def test_surname_sort_ignores_the_given_name():
    """"Airlie J. McCoy" files under M. Filing it under A is the bug
    that makes an alphabetical list useless."""
    assert index.surname_key("Airlie J. McCoy")[0] == "mccoy"
    assert index.surname_key("M.E.M. Noble")[0] == "noble"
    assert index.surname_key("Garib N. Murshudov")[0] == "murshudov"


def test_surname_sort_folds_accents():
    """"Ana Casañal" belongs with the Cs, not after Z."""
    assert index.surname_key("Ana Casañal")[0] == "casanal"
    ordered = sorted(["Ana Casañal", "Jon Agirre", "Tom Burnley"],
                     key=index.surname_key)
    assert ordered == ["Jon Agirre", "Tom Burnley", "Ana Casañal"]


def test_surname_order(conn):
    add(conn, "A1", "Airlie J. McCoy")
    add(conn, "A2", "Jon Agirre")
    add(conn, "A3", "Ana Casañal")

    assert names(conn, "surname") == [
        "Jon Agirre", "Ana Casañal", "Airlie J. McCoy"]
    # The stored order is untouched by looking at it another way.
    assert names(conn, "custom") == [
        "Airlie J. McCoy", "Jon Agirre", "Ana Casañal"]


def test_date_added_is_not_the_custom_order(conn):
    """The two agree until the first drag, and diverge for good after
    it — which is why "as now" was never date-added."""
    add(conn, "A1", "First Added", added_at="2026-01-01")
    add(conn, "A2", "Second Added", added_at="2026-02-01")
    add(conn, "A3", "Third Added", added_at="2026-03-01")

    index.move_author_trail(conn, "A3", 0)

    assert names(conn, "custom")[0] == "Third Added"
    assert names(conn, "added") == [
        "First Added", "Second Added", "Third Added"]


def test_first_publication_order_puts_the_unknown_last(conn):
    """An author whose page has never been opened has no cached year.
    Sorting them ahead of 1989 would read as "published first"."""
    add(conn, "A1", "Newer Person", first_year=2022)
    add(conn, "A2", "Older Person", first_year=1989)
    add(conn, "A3", "Unopened Person")

    assert names(conn, "first_publication") == [
        "Older Person", "Newer Person", "Unopened Person"]


def test_the_year_cache_only_writes_a_change(conn):
    add(conn, "A1", "Someone")

    assert index.set_author_first_publication_year(conn, "A1", 1999) is True
    assert index.set_author_first_publication_year(conn, "A1", 1999) is False
    assert index.set_author_first_publication_year(conn, "A1", 1987) is True
    # Nonsense is refused rather than stored.
    assert index.set_author_first_publication_year(conn, "A1", None) is False
    assert index.set_author_first_publication_year(conn, "A1", "soon") is False

    row = index.list_author_trail(conn)[0]
    assert row["first_publication_year"] == 1987


def test_an_unknown_order_falls_back_to_custom(conn):
    add(conn, "A1", "Zoe Zed")
    add(conn, "A2", "Al Abel")
    assert names(conn, "nonsense") == names(conn, "custom")


def test_committing_a_displayed_order_as_custom(conn):
    """What a drag inside a sorted view needs: freeze what is on
    screen, so the subsequent move renumbers against what the user can
    actually see."""
    add(conn, "A1", "Airlie J. McCoy")
    add(conn, "A2", "Jon Agirre")
    add(conn, "A3", "Ana Casañal")

    shown = [r["key"] for r in index.list_author_trail(conn, "surname")]
    index.set_author_trail_order(conn, shown)

    assert [r["key"] for r in index.list_author_trail(conn)] == shown
    assert names(conn, "custom") == names(conn, "surname")


def test_committing_an_order_cannot_lose_a_row(conn):
    """A caller working from a stale list must not drop anyone."""
    add(conn, "A1", "One")
    add(conn, "A2", "Two")
    add(conn, "A3", "Three")

    index.set_author_trail_order(conn, ["A3", "A9-not-here"])

    keys = [r["key"] for r in index.list_author_trail(conn)]
    assert keys[0] == "A3"
    assert sorted(keys) == ["A1", "A2", "A3"]


def test_the_trail_order_preference_round_trips(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    import importlib
    from alexandria import prefs
    importlib.reload(prefs)

    assert prefs.get_author_trail_sort() == "custom"
    prefs.set_author_trail_sort("surname")
    assert prefs.get_author_trail_sort() == "surname"
    # A value from a hand-edited config that we no longer understand
    # must not wedge the sidebar.
    prefs.set_author_trail_sort("by-vibes")
    assert prefs.get_author_trail_sort() == "surname"
    importlib.reload(prefs)
