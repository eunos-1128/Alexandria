"""Looking a paper up from {first author, year, journal}.

The machinery landed on 2026-09-06 —
`metrics.find_citation_candidates`, with journal-abbreviation
expansion and an exact-year-then-±1-then-any ladder — but the only
door to it was the Edit-metadata dialog of a paper *already in the
library*. That is the wrong way round: the case for this is a
reference you do not have. Asked for again 2026-09-26: "I have
{first_author, year, journal} and I want to look up the reference."

So: a Discover tab. Two ways in, because a citation arrives in two
forms — pasted whole from an email or a talk, or read off a page into
three fields. The parse fills the fields rather than searching behind
them, so its interpretation is visible and correctable; a misread
surname is otherwise indistinguishable from a paper that isn't there.

Verified live against OpenAlex on 2026-09-26, top hit in each case:

    Jones  / J. Mol. Biol. / 1995 → GOLD docking  (1,553 citations)
    Jones  / Acta Cryst. A / 1991 → the O paper  (12,692)
    Emsley / Acta Cryst. D / 2010 → Coot  (30,406)
    Jumper / Nature       / 2021 → AlphaFold  (47,584)
"""

import os
import sys
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import discover, metrics

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(not _display_ok,
                                reason="no display for GTK tests")


# ---- the candidate → row adapter -------------------------------------

def test_a_candidate_is_reshaped_for_the_row_builder():
    """`find_citation_candidates` says `cited_by_count`;
    `_build_related_row` reads `citations`. One key apart is enough to
    make every candidate look uncited."""
    row = discover._as_work_row(
        {"doi": "10.1107/s0907444910007493", "title": "Coot",
         "cited_by_count": 30406, "year": 2010})

    assert row["citations"] == 30406
    assert row["title"] == "Coot"
    assert row["doi"] == "10.1107/s0907444910007493"


def test_a_candidate_with_no_citations_is_zero_not_missing():
    assert discover._as_work_row({"title": "x"})["citations"] == 0


# ---- the page --------------------------------------------------------

class _Page:
    """The citation page's methods on a stand-in, so the page is built
    without a Discover window, a parent or a database."""

    _build_citation_page = discover.DiscoverWindow._build_citation_page
    _on_citation_paste = discover.DiscoverWindow._on_citation_paste
    _on_citation_paste_changed = \
        discover.DiscoverWindow._on_citation_paste_changed
    _on_citation_search = discover.DiscoverWindow._on_citation_search
    _after_citation_search = discover.DiscoverWindow._after_citation_search
    _YEAR_MODE_NOTE = discover.DiscoverWindow._YEAR_MODE_NOTE

    def __init__(self):
        self.searched = []
        self.rows = []
        self.parent_window = types.SimpleNamespace(
            _existing_dois_set=lambda: set())
        self.page = self._build_citation_page()

    def _clear_box(self, _box):
        self.rows = []

    def _build_work_row(self, r, _existing):
        self.rows.append(r)
        return Gtk.Label(label=r.get("title") or "")

    # Typing, the way a person does it.
    def type_citation(self, text):
        self._ci_paste.set_text(text)

    @property
    def fields(self):
        return (self._ci_surname.get_text(),
                self._ci_year.get_text(),
                self._ci_journal.get_text())


def test_a_pasted_citation_fills_the_three_fields():
    p = _Page()

    p.type_citation("Jones et al., J. Mol. Biol., 1995")

    assert p.fields == ("Jones", "1995", "J. Mol. Biol.")


def test_a_citation_with_the_year_in_brackets_too():
    p = _Page()

    p.type_citation("Sheldrick (2008) Acta Cryst A")

    assert p.fields == ("Sheldrick", "2008", "Acta Cryst A")


def test_what_the_user_types_is_not_overwritten():
    """Correcting the parse is the whole point of showing it."""
    p = _Page()
    p.type_citation("Jones et al., J. Mol. Biol., 1995")

    p._ci_surname.set_text("Jonas")           # a hand edit
    p.type_citation("Jones et al., J. Mol. Biol., 1996")

    surname, year, _journal = p.fields
    assert surname == "Jonas", "the hand edit survives"
    assert year == "1996", "the untouched field still tracks"


def test_a_hand_edit_survives_the_next_paste_too():
    """Once the user has corrected a field it is theirs, not the
    parser's, until they empty it again."""
    p = _Page()
    p.type_citation("Jones et al., JMB, 1995")
    p._ci_journal.set_text("Journal of Molecular Biology")

    p.type_citation("Read, Acta Cryst. A, 1986")

    assert p.fields == ("Read", "1986", "Journal of Molecular Biology")


def test_clearing_a_field_hands_it_back_to_the_parser():
    """An empty box holds nothing worth protecting."""
    p = _Page()
    p.type_citation("Jones et al., JMB, 1995")
    p._ci_journal.set_text("Journal of Molecular Biology")
    p._ci_journal.set_text("")

    p.type_citation("Read, Acta Cryst. A, 1986")

    assert p.fields == ("Read", "1986", "Acta Cryst. A")


def test_the_parser_can_fill_the_same_field_twice():
    """The guard that stops `set_text` counting as a hand edit."""
    p = _Page()

    p.type_citation("Jones et al., JMB, 1995")
    p.type_citation("Emsley, Acta Cryst. D, 2010")

    assert p.fields == ("Emsley", "2010", "Acta Cryst. D")


# ---- what it refuses to search for -----------------------------------

def test_a_surname_is_the_one_thing_required():
    p = _Page()
    p._ci_journal.set_text("Nature")
    p._ci_year.set_text("2021")

    p._on_citation_search(None)

    assert "surname" in p._ci_status.get_text()


def test_a_year_that_is_not_a_year_is_refused():
    p = _Page()
    p._ci_surname.set_text("Jones")
    p._ci_year.set_text("nineteen ninety five")

    p._on_citation_search(None)

    assert p._ci_status.get_text() == "Year must be a number."


def test_author_alone_is_allowed(monkeypatch):
    """Year and journal are both optional — the ladder drops the year
    anyway when it finds nothing, and a bare surname is a legitimate
    (if broad) search."""
    asked = []
    monkeypatch.setattr(metrics, "find_citation_candidates",
                        lambda *a, **k: asked.append(a) or (None, []))
    p = _Page()
    p._ci_surname.set_text("Sheldrick")

    p._on_citation_search(None)

    said = p._ci_status.get_text()
    assert "Searching OpenAlex" in said
    assert "any year" in said and "any journal" in said
    # The worker thread is the only thing that touches the network;
    # give it a moment and check what it was asked for.
    for _ in range(200):
        if asked:
            break
        time.sleep(0.005)
    assert asked == [("Sheldrick", None, None)]


# ---- how the results are described -----------------------------------

CAND = {"doi": "10.1016/s0022-2836(95)80037-9",
        "title": "Molecular recognition of receptor sites",
        "year": 1995, "journal": "Journal of Molecular Biology",
        "first_author": "Gareth Jones", "cited_by_count": 1553}


def test_results_are_listed_best_first():
    p = _Page()

    p._after_citation_search("exact", [CAND, dict(CAND, title="second")],
                             None)

    assert [r["title"] for r in p.rows] == [CAND["title"], "second"]
    assert p.rows[0]["citations"] == 1553
    assert "2 candidates" in p._ci_status.get_text()


def test_an_exact_year_match_needs_no_explanation():
    p = _Page()

    p._after_citation_search("exact", [CAND], None)

    assert p._ci_status.get_text() == "1 candidate, best first"


def test_a_relaxed_year_says_so():
    """A result found only after the year was loosened is a weaker
    answer, and the user is the one who knows whether that matters."""
    p = _Page()

    p._after_citation_search("minus1", [CAND], None)
    assert "year before" in p._ci_status.get_text()

    p._after_citation_search("none", [CAND], None)
    assert "year was dropped" in p._ci_status.get_text()


def test_nothing_found_suggests_what_to_loosen():
    p = _Page()

    p._after_citation_search(None, [], None)

    said = p._ci_status.get_text()
    assert "as printed on the paper" in said, "names the usual cause"
    assert "without the journal" in said


def test_a_failed_search_is_not_silent():
    p = _Page()

    p._after_citation_search(None, [], "HTTP 429")

    assert "HTTP 429" in p._ci_status.get_text()


# ---- the tab exists --------------------------------------------------

def test_the_page_builder_is_wired_into_the_window():
    import inspect

    src = inspect.getsource(discover.DiscoverWindow.__init__)
    assert '"citation", "By citation"' in src
    assert "_build_citation_page()" in src


def test_the_parser_used_is_the_shared_one():
    """No second citation parser: the Edit dialog and this tab read a
    citation the same way."""
    import inspect

    src = inspect.getsource(
        discover.DiscoverWindow._on_citation_paste_changed)
    assert "metrics.parse_citation_hint" in src
    assert callable(metrics.parse_citation_hint)
