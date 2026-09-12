"""The sidebar's sort control, at the widget level.

The index-level orders are covered next door; what matters here is
that switching order redraws the rows without disturbing anything
else, and that a drag inside a sorted view does not quietly scramble
the arrangement the user built under Custom — the one way this feature
could destroy something they had made by hand.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw, GLib
    _display_ok = bool(Gtk.init_check())
    if _display_ok:
        Adw.init()
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(
    not _display_ok, reason="no display for GTK tests")

from alexandria import author_works, index


@pytest.fixture
def window(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    import importlib
    from alexandria import prefs
    importlib.reload(prefs)
    monkeypatch.setattr(author_works, "_prefs", prefs)

    # Selecting a row builds an AuthorPage, which fetches a profile in
    # a thread. Nothing here is about that, and a widget test has no
    # business asking OpenAlex about three invented IDs.
    from alexandria import metrics
    monkeypatch.setattr(metrics, "fetch_author_profile",
                        lambda *a, **k: None)
    monkeypatch.setattr(metrics, "fetch_coauthors", lambda *a, **k: [])
    monkeypatch.setattr(author_works.AuthorPage, "_cached_or_fetch_works",
                        lambda *a, **k: [])

    conn = index.open_db(str(tmp_path / "lib" / "library.db"))
    for key, name in (("A1", "Airlie J. McCoy"),
                      ("A2", "Jon Agirre"),
                      ("A3", "Ana Casañal")):
        index.add_author_trail(conn, {"openalex_id": key, "name": name})

    win = author_works.AuthorsWindow(conn)
    yield win
    win.destroy()
    importlib.reload(prefs)


def shown(win):
    out, i = [], 0
    row = win.sidebar.get_row_at_index(0)
    while row is not None:
        out.append(row.trail_entry["name"])
        i += 1
        row = win.sidebar.get_row_at_index(i)
    return out


def test_the_sidebar_opens_in_custom_order(window):
    assert window._sort == "custom"
    assert shown(window) == [
        "Airlie J. McCoy", "Jon Agirre", "Ana Casañal"]


def test_switching_order_redraws_the_rows(window):
    window._set_sort("surname")

    assert shown(window) == [
        "Jon Agirre", "Ana Casañal", "Airlie J. McCoy"]
    # One row per author, not two: a rebuild that forgot to remove the
    # old widgets would still look right at the top of the list.
    assert len(window._rows) == 3
    assert len(shown(window)) == 3


def test_the_choice_survives_a_new_window(window, tmp_path):
    window._set_sort("surname")

    second = author_works.AuthorsWindow(window.conn)
    try:
        assert second._sort == "surname"
        assert shown(second)[0] == "Jon Agirre"
    finally:
        second.destroy()


def test_selection_survives_a_reorder(window):
    window.sidebar.select_row(window._rows["A3"])
    assert window.stack.get_visible_child_name() == "A3"

    window._set_sort("surname")

    assert window.sidebar.get_selected_row() is window._rows["A3"]
    assert window.stack.get_visible_child_name() == "A3"


def test_dragging_in_a_sorted_view_commits_that_order(window):
    """The protective case. Dragging while sorted by surname must not
    renumber against positions the user cannot see — it takes the
    displayed order as the new arrangement and switches to Custom."""
    window._set_sort("surname")

    # Move McCoy (displayed last, under surname) to the top.
    window._reorder("A1", 0)

    assert window._sort == "custom"
    assert shown(window) == [
        "Airlie J. McCoy", "Jon Agirre", "Ana Casañal"]
    # And that is now what the database says, for the next window too.
    assert [r["name"] for r in index.list_author_trail(window.conn)] == [
        "Airlie J. McCoy", "Jon Agirre", "Ana Casañal"]


class _ImmediateThread:
    """Runs the target on this thread, so the test is not racing a
    daemon it cannot join."""

    def __init__(self, target=None, args=(), kwargs=None, daemon=None):
        self._target, self._args = target, args
        self._kwargs = kwargs or {}

    def start(self):
        self._target(*self._args, **self._kwargs)


def test_choosing_first_publication_backfills_the_missing_years(
        window, monkeypatch):
    """Otherwise the sort is useless the first time it is used: years
    are cached per visited author, so an unvisited trail sorts as all
    "unknown" and collapses to alphabetical."""
    from alexandria import metrics

    asked = []

    def fake_profile(orcid=None, openalex_id=None):
        asked.append(openalex_id or orcid)
        return {"counts_by_year": [
            {"year": 2000 + len(asked), "works_count": 2,
             "cited_by_count": 1}]}

    monkeypatch.setattr(metrics, "fetch_author_profile", fake_profile)
    monkeypatch.setattr(author_works.threading, "Thread", _ImmediateThread)

    window._set_sort("first_publication")

    assert sorted(asked) == ["A1", "A2", "A3"]
    years = [r["first_publication_year"]
             for r in index.list_author_trail(window.conn)]
    assert sorted(years) == [2001, 2002, 2003]


def test_the_backfill_stops_when_openalex_is_refusing(window, monkeypatch):
    """An unkeyed or rate-limited session must not spend a request per
    author to sort a list."""
    from alexandria import metrics

    asked = []
    monkeypatch.setattr(metrics, "fetch_author_profile",
                        lambda **k: asked.append(k) or None)
    monkeypatch.setattr(metrics, "openalex_paused_until", lambda: 1.0)
    monkeypatch.setattr(author_works.threading, "Thread", _ImmediateThread)

    window._set_sort("first_publication")

    assert asked == []


def test_the_backfill_runs_once_per_session(window):
    calls = []
    window._first_year_worker = lambda rows: calls.append(len(rows))

    window._set_sort("first_publication")
    window._set_sort("surname")
    window._set_sort("first_publication")

    assert len(calls) == 1


def test_the_menu_offers_every_order(window):
    menu = window._build_sort_button().get_menu_model()
    targets = set()
    for i in range(menu.get_n_items()):
        value = menu.get_item_attribute_value(i, "target", None)
        if value is not None:
            targets.add(value.get_string())
    assert targets == set(index.TRAIL_SORTS)


def test_the_action_state_tracks_the_order(window):
    """The menu renders radio items from the action state, so a state
    that lagged would tick the wrong row."""
    window._on_sort_action(window._sort_action,
                           GLib.Variant.new_string("added"))
    assert window._sort_action.get_state().get_string() == "added"
    assert window._sort == "added"
