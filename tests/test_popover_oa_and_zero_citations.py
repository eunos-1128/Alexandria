"""Two small things from the other machine's inbox, 2026-09-15/16.

**Open-access status in the Cited-by and Related-works popovers.** The
row builder has carried an OA chip for a long time, with a comment
admitting that nothing filled in its data, so it never appeared. The
fix is on the fetch side: two more fields in a `select` that is being
sent anyway. The chip is now the cards' own, so it carries the status
(Gold / Green / Hybrid …) rather than a bare "OA", because that is the
useful answer: gold and hybrid mean the publisher's copy is free,
green only that a repository holds one.

**The Cited-by button is greyed when the count is known to be zero.**
The card already says "cited 0×" beside it. The distinction that
matters is `None` against `0`: `None` means never fetched, and greying
that would hide a working button on a guess.
"""

import os
import sys
import urllib.parse

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

from alexandria import metrics

needs_display = pytest.mark.skipif(not _display_ok,
                                   reason="no display for GTK tests")


def work(wid, status, pdf=None, landing=None):
    return {
        "id": "https://openalex.org/" + wid,
        "doi": "https://doi.org/10.1/" + wid.lower(),
        "title": "Paper " + wid, "publication_year": 2024,
        "authorships": [], "primary_location": {"source": {}},
        "cited_by_count": 3,
        "open_access": {"is_oa": status != "closed", "oa_status": status},
        "best_oa_location": ({"pdf_url": pdf, "landing_page_url": landing}
                             if (pdf or landing) else None),
    }


# ---- the fetch side --------------------------------------------------

def test_oa_fields_read_the_status_and_the_best_link():
    got = metrics._oa_fields(work("W1", "green", pdf="https://r/p.pdf",
                                  landing="https://r/landing"))

    assert got == {"is_oa": True, "oa_status": "green",
                   "oa_url": "https://r/p.pdf"}


def test_oa_fields_fall_back_to_the_landing_page():
    got = metrics._oa_fields(work("W1", "hybrid", landing="https://j/a"))
    assert got["oa_url"] == "https://j/a"


def test_oa_fields_tolerate_a_work_without_them():
    """A cached or older row fetched without the new fields."""
    got = metrics._oa_fields({"id": "W1"})

    assert got == {"is_oa": False, "oa_status": None, "oa_url": None}


def _capture(monkeypatch, responses):
    """Serve `responses` in order; record every URL asked for."""
    asked = []
    queue = list(responses)

    def fake(url, headers=None, timeout=None, **_k):
        asked.append(url)
        return queue.pop(0)

    monkeypatch.setattr(metrics, "_http_get_json", fake)
    return asked


def _select_of(url):
    q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    return q.get("select", "")


def test_cited_by_asks_for_oa_in_the_same_request(monkeypatch):
    asked = _capture(monkeypatch, [
        {"results": [work("W2", "gold"), work("W3", "closed")]}])

    rows = metrics.fetch_cited_by(openalex_id="W1")

    # One request — the OA data rides along, it does not cost a trip.
    assert len(asked) == 1
    assert "open_access" in _select_of(asked[0])
    assert "best_oa_location" in _select_of(asked[0])
    assert [(r["is_oa"], r["oa_status"]) for r in rows] == [
        (True, "gold"), (False, "closed")]


def test_related_works_asks_for_oa_in_the_batched_request(monkeypatch):
    asked = _capture(monkeypatch, [
        {"related_works": ["https://openalex.org/W2"]},
        {"results": [work("W2", "hybrid")]},
    ])

    rows = metrics.fetch_related_works(openalex_id="W1")

    assert len(asked) == 2
    assert "open_access" in _select_of(asked[1])
    assert rows[0]["oa_status"] == "hybrid"


# ---- the popover row -------------------------------------------------

def _labels(widget):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Label):
            out.append(w.get_text())
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()

    walk(widget)
    return out


@needs_display
@pytest.mark.parametrize("status,label", [
    ("gold", "Gold OA"), ("green", "Green OA"), ("hybrid", "Hybrid OA")])
def test_the_row_shows_which_kind_of_open_access(status, label):
    from alexandria import browse

    row = {"title": "A citing paper", "doi": "10.1/x", "year": 2024,
           "first_author": "A", "last_author": "B", "journal": "J",
           "citations": 2, "is_oa": True, "oa_status": status,
           "oa_url": "https://example.org/x.pdf"}

    widget = browse.BrowserWindow._build_related_row(
        browse.BrowserWindow.__new__(browse.BrowserWindow), row, set())

    assert label in _labels(widget)


@needs_display
def test_a_closed_paper_gets_no_chip():
    from alexandria import browse

    row = {"title": "Paywalled", "doi": "10.1/y", "year": 2024,
           "is_oa": False, "oa_status": "closed"}

    widget = browse.BrowserWindow._build_related_row(
        browse.BrowserWindow.__new__(browse.BrowserWindow), row, set())

    assert not any(t.endswith("OA") for t in _labels(widget))


# ---- the card's Cited-by button -----------------------------------------

def _card_for(tmp_path, citations):
    from alexandria import browse, index, sidecar

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    rec = sidecar.new_record(str(pdf))
    rec.update({"title": "T", "doi": "10.1/x", "year": 2020,
                "citations": citations})
    sc = sidecar.sidecar_path_for(str(pdf))
    sidecar.write(sc, rec)
    conn = index.open_db(str(tmp_path / "lib.db"))
    index.upsert(conn, str(pdf), sc, None, rec, os.path.getmtime(sc))
    row = dict(conn.execute("SELECT * FROM papers").fetchone())

    class Window:
        """Only what a card reads while it is built; nothing here is
        clicked."""

    return browse.make_card(row, Window(), conn, lambda: None)


def _cited_by_button(card):
    found = []

    def walk(w):
        tip = w.get_tooltip_text() or ""
        if isinstance(w, Gtk.Button) and (tip.startswith("Cited by")
                                          or "cite this one" in tip):
            found.append(w)
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()

    walk(card)
    assert len(found) == 1, "expected exactly one Cited-by button"
    return found[0]


@needs_display
def test_a_paper_nobody_cites_has_a_greyed_button(tmp_path):
    btn = _cited_by_button(_card_for(tmp_path, 0))

    assert btn.get_sensitive() is False
    assert "No papers cite this one yet" in btn.get_tooltip_text()


@needs_display
def test_an_unfetched_count_keeps_the_button_live(tmp_path):
    """The whole point of `== 0`: `None` is "we have not asked", not
    "the answer is none"."""
    btn = _cited_by_button(_card_for(tmp_path, None))

    assert btn.get_sensitive() is True


@needs_display
def test_a_cited_paper_keeps_the_button_live(tmp_path):
    btn = _cited_by_button(_card_for(tmp_path, 5))

    assert btn.get_sensitive() is True
