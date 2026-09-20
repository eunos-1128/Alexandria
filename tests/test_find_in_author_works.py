"""The find bar on an author's works list, wired up.

The rule it exists to keep: **every row stays on screen**. The list is
ordered by date or by citations, and that order answers "where in the
career", which filtering would throw away. So these tests check the
row count never moves, that the matched rows are the ones marked, and
that stepping walks the matches and wraps.
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
    from gi.repository import Gtk, Adw
    _display_ok = bool(Gtk.init_check())
    if _display_ok:
        Adw.init()
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(not _display_ok,
                                reason="no display for GTK tests")

from alexandria import author_works, index

WORKS = [
    {"title": "Cryo-EM of the ribosome", "authors": ["A Smith", "B Jones"],
     "year": 2024, "journal": "Nature", "doi": "10.1/a", "citations": 40},
    {"title": "Ribosome assembly pathways", "authors": ["C Cryo"],
     "year": 2023, "journal": "Cell", "doi": "10.1/b", "citations": 10},
    {"title": "Unrelated methods paper", "authors": ["D Brown"],
     "year": 2022, "journal": "Science", "doi": "10.1/c", "citations": 2},
]


@pytest.fixture
def page(tmp_path, monkeypatch):
    """A real AuthorPage with the network stubbed out."""
    monkeypatch.setattr(author_works.metrics, "fetch_author_profile",
                        lambda *a, **k: None)
    monkeypatch.setattr(author_works.metrics, "fetch_coauthors",
                        lambda *a, **k: [])
    monkeypatch.setattr(author_works.AuthorPage, "_cached_or_fetch_works",
                        lambda *a, **k: [])
    conn = index.open_db(str(tmp_path / "lib.db"))
    p = author_works.AuthorPage(
        conn, {"name": "A Person", "openalex_id": "A1"})
    for w in WORKS:
        p.list_box.append(p._make_work_row(w))
    return p


def _type(page, text):
    """Type into the find box and let the loop run.

    `Gtk.SearchEntry` debounces `search-changed` — which is what keeps
    a 50-row list from being re-marked on every keystroke — so the
    signal has not fired when `set_text` returns."""
    import time
    from gi.repository import GLib
    page._find_entry.set_text(text)
    deadline = time.time() + 2.0
    ctx = GLib.MainContext.default()
    while time.time() < deadline:
        while ctx.pending():
            ctx.iteration(False)
        if page._find_count.get_text() or not text:
            # An empty box legitimately clears the count, so give the
            # idle queue one pass and stop.
            break
        time.sleep(0.01)


def _rows(page):
    out, child = [], page.list_box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _markup_of(page, row_idx):
    """Every label's markup in one row, joined."""
    rec = page._find_rows[row_idx]
    return " ".join(lbl.get_label() for lbl, _t, _w in rec["fields"])


def test_nothing_is_hidden_when_searching(page):
    before = len(_rows(page))

    _type(page, "cryo")

    assert len(_rows(page)) == before == 3
    assert all(r.get_visible() for r in _rows(page))


def test_the_matching_rows_are_marked(page):
    _type(page, "cryo")

    assert author_works.find_text.HIGHLIGHT_BG in _markup_of(page, 0)
    # The second row matches on its *author*, "C Cryo" — a field the
    # reader can see, so it counts.
    assert author_works.find_text.HIGHLIGHT_BG in _markup_of(page, 1)
    assert author_works.find_text.HIGHLIGHT_BG not in _markup_of(page, 2)


def test_the_count_reads_rows_of_rows(page):
    _type(page, "cryo")
    assert page._find_count.get_text() == "2 of 3"

    _type(page, "ribosome")
    assert page._find_count.get_text() == "2 of 3"

    _type(page, "zzz")
    assert page._find_count.get_text() == "no matches"


def test_clearing_the_box_puts_the_rows_back_unmarked(page):
    _type(page, "cryo")
    assert author_works.find_text.HIGHLIGHT_BG in _markup_of(page, 0)

    _type(page, "")

    assert page._find_count.get_text() == ""
    assert all(author_works.find_text.HIGHLIGHT_BG not in _markup_of(page, i)
               for i in range(3))
    assert len(_rows(page)) == 3


def test_stepping_walks_the_matches_and_wraps(page):
    stepped = []
    page._scroll_row_into_view = lambda row: stepped.append(row)

    _type(page, "cryo")      # lands on the first match
    assert page._find_at == 0

    page._step_find(1)
    assert page._find_at == 1
    page._step_find(1)
    assert page._find_at == 0, "wraps round, like a find bar"
    page._step_find(-1)
    assert page._find_at == 1

    assert stepped, "each step scrolls its row into view"


def test_the_step_buttons_appear_only_when_there_is_a_choice(page):
    _type(page, "cryo")            # two matches
    assert page._find_next_btn.get_visible() is True

    _type(page, "unrelated")       # one match
    assert page._find_next_btn.get_visible() is False
    assert page._find_count.get_text() == "1 of 3"


def test_a_row_built_while_a_search_is_live_arrives_marked(page):
    """Rows come back from a re-fetch or a sort change with the entry
    still full."""
    _type(page, "cryo")

    page.list_box.append(page._make_work_row(
        {"title": "Cryo-ET tomography", "authors": ["E White"],
         "year": 2021, "journal": "eLife", "doi": "10.1/d"}))

    assert author_works.find_text.HIGHLIGHT_BG in _markup_of(page, 3)
    assert page._find_count.get_text() == "3 of 4"
