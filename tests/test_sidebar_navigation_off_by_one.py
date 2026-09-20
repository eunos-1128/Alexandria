"""Clicking a page in the sidebar goes to *that* page.

Reported 2026-09-20: clicking the thumbnail labelled "2" arrives at
page 3, on every PDF tried.

`_goto` counts from zero — `_goto(0)` is the first page, and the
page-number box converts with `int(text) - 1` before calling it. Both
sidebar modes were handing it a number that had already been converted
for display: a cell built for index `i` shows `str(i + 1)` and called
`_goto(i + 1)`. The outline is the same shape — `dest_page_index`
subtracts 1 from Poppler's 1-based destination, so `entry["page"]` is
an index too, and its row also showed `page + 1` and jumped to
`page + 1`.

Both are tested here by emitting the gesture the user's click emits,
so the wiring is exercised rather than the arithmetic restated.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(not _display_ok,
                                reason="no display for GTK tests")

from alexandria import viewer

N_PAGES = 5


class _Sidebar:
    """The two row builders, on a stand-in that records where a click
    would take the reader."""

    _fill_thumbnails = viewer.PdfViewerWindow._fill_thumbnails
    _build_outline_row = viewer.PdfViewerWindow._build_outline_row
    _draw_thumb = viewer.PdfViewerWindow._draw_thumb

    def __init__(self):
        self.n_pages = N_PAGES
        self.doc = types.SimpleNamespace(
            get_page=lambda i: types.SimpleNamespace(
                get_size=lambda: (612.0, 792.0)))
        self.thumb_widgets = {}
        self.thumb_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.went_to = []
        self._thumb_cache = {}

    def _goto(self, n):
        self.went_to.append(n)

    def _thumb_near_viewport(self, _i):
        return False


def _cells(box):
    out = []
    child = box.get_first_child()
    while child is not None:
        out.append(child)
        child = child.get_next_sibling()
    return out


def _click(widget):
    """Emit what a real click emits, on the widget's own gesture."""
    for ctrl in widget.observe_controllers():
        if isinstance(ctrl, Gtk.GestureClick):
            ctrl.emit("released", 1, 0.0, 0.0)
            return True
    return False


def _label_texts(widget):
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


def test_the_thumbnail_labelled_two_goes_to_page_two():
    side = _Sidebar()
    side._fill_thumbnails()
    cells = _cells(side.thumb_box)

    assert "2" in _label_texts(cells[1]), "the second cell is labelled 2"
    assert _click(cells[1])

    # Page 2 of the document is index 1. Index 2 would be page 3 —
    # the reported bug.
    assert side.went_to == [1]


def test_every_thumbnail_goes_to_its_own_page():
    side = _Sidebar()
    side._fill_thumbnails()

    for cell in _cells(side.thumb_box):
        _click(cell)

    assert side.went_to == list(range(N_PAGES))


def test_an_outline_row_goes_to_the_page_it_shows():
    """`entry["page"]` is already an index: dest_page_index() turns
    Poppler's 1-based destination into one."""
    side = _Sidebar()
    row = side._build_outline_row(
        {"title": "Results", "page": 3, "depth": 0})

    assert "4" in _label_texts(row), "shown to the reader as page 4"
    assert _click(row)
    assert side.went_to == [3]


def test_an_outline_row_without_a_page_is_not_clickable():
    side = _Sidebar()
    row = side._build_outline_row(
        {"title": "Front matter", "page": None, "depth": 0})

    assert _click(row) is False
    assert side.went_to == []


# ---- the highlights list ---------------------------------------------

def test_clicking_a_highlight_row_navigates_not_just_its_arrow():
    """Reported as "navigation by highlighted text is not working".
    The jump worked; only the 16 px arrow on the right was wired to
    it, while Contents and Pages both go where you click."""
    side = _Sidebar()
    side.highlights = []
    side.sidecar_path = "/x/p.pdf.alexandria"
    jumped = []
    side._scroll_to_highlight = lambda h: jumped.append(h)

    h = {"page": 3, "text": "dual-space direct methods",
         "comment": "the key claim", "quads": [[10.0, 100.0, 80.0, 12.0]]}
    row = viewer.PdfViewerWindow._build_highlight_row(side, h)

    assert _click(row), "the row itself carries a click gesture"
    assert jumped == [h]


def test_the_arrow_still_works_too():
    side = _Sidebar()
    jumped = []
    side._scroll_to_highlight = lambda h: jumped.append(h)
    h = {"page": 1, "text": "a phrase", "quads": []}

    row = viewer.PdfViewerWindow._build_highlight_row(side, h)
    buttons = []

    def walk(w):
        if isinstance(w, Gtk.Button):
            buttons.append(w)
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()

    walk(row)
    assert buttons, "the arrow is still there as the affordance"
    buttons[-1].emit("clicked")
    assert jumped == [h]
