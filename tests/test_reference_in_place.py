"""Clicking a citation shows the reference where the reader is.

Proposed 2026-09-10 and built 2026-09-19. The popover has always shown
the full reference text, so moving the reader to the bibliography *and*
showing them the entry they had been moved to was doing the job twice.
The move was the half with a cost: it had to be undone, which is what
the jump stack, "Back to text" and Alt-Left exist for.

The rule: **jump only when there is no text to show.** A parsed entry
opens the popover in place, with "Go to reference" offering the trip
on request. An unparsed entry, or a link with no reference number,
falls back to the old behaviour, because moving to where the link
points is then all there is.

Like `test_jump_history`, the click logic runs as the real methods
bound onto a stand-in, which keeps it off GTK's display. The popover
itself is built for real further down.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import viewer

RECT = (100.0, 700.0, 110.0, 690.0)
TARGET_PAGE, TARGET_TOP = 9, 400.0


class _Viewer:
    """Just enough of the viewer for the click handler."""

    _handle_citation_click = viewer.PdfViewerWindow._handle_citation_click
    _has_reference_text = viewer.PdfViewerWindow._has_reference_text

    def __init__(self, ref_n, entries):
        self._link = (RECT, TARGET_PAGE, TARGET_TOP, ref_n)
        self._bibliography_by_n = entries
        self.doc = types.SimpleNamespace(get_page=lambda i: object())
        self.pushed = 0
        self.jumped_to = []

    def _citation_at(self, page_idx, x, y):
        return self._link

    def _ensure_bibliography_parsed(self):
        pass

    def _push_jump_origin(self):
        self.pushed += 1

    def _jump_to(self, page, top=None):
        self.jumped_to.append((page, top))

    def _show_reference_popover(self, *args):
        pass


@pytest.fixture
def idle(monkeypatch):
    """Record what the click schedules, instead of running it later."""
    calls = []
    monkeypatch.setattr(viewer.GLib, "idle_add",
                        lambda fn, *args: calls.append((fn, args)))
    monkeypatch.setattr(viewer.references_pdf, "citation_context",
                        lambda page, rect: "the sentence it was cited in")
    return calls


ENTRY = {12: {"n": 12, "text": "Sheldrick GM (2008) A short history "
                               "of SHELX. Acta Cryst A64:112-122",
              "doi": None}}


def test_a_parsed_reference_opens_in_place(idle):
    v = _Viewer(12, ENTRY)

    assert v._handle_citation_click(0, 105.0, 95.0) is True

    assert v.jumped_to == [], "the page must not move"
    assert v.pushed == 0, "nothing to come back from"
    (fn, args), = idle
    assert fn == v._show_reference_popover
    ref_n, page, top, context, jumped = args
    assert (ref_n, page, top, jumped) == (12, TARGET_PAGE, TARGET_TOP,
                                          False)
    # Where the trip would go is still passed along, for "Go to
    # reference".
    assert context == "the sentence it was cited in"


def test_an_unparsed_reference_still_jumps(idle):
    """Nothing to show, so moving to it is all there is."""
    v = _Viewer(12, {})

    v._handle_citation_click(0, 105.0, 95.0)

    assert v.pushed == 1
    assert v.jumped_to == [(TARGET_PAGE, TARGET_TOP)]
    (_fn, args), = idle
    assert args[-1] is True, "the popover must know the jump happened"


def test_an_entry_with_empty_text_counts_as_unparsed(idle):
    v = _Viewer(12, {12: {"n": 12, "text": "   ", "doi": None}})

    v._handle_citation_click(0, 105.0, 95.0)

    assert v.jumped_to == [(TARGET_PAGE, TARGET_TOP)]


def test_a_link_with_no_reference_number_jumps_without_a_popover(idle):
    v = _Viewer(None, ENTRY)

    v._handle_citation_click(0, 105.0, 95.0)

    assert v.jumped_to == [(TARGET_PAGE, TARGET_TOP)]
    assert idle == []


def test_a_miss_is_not_handled(idle):
    v = _Viewer(12, ENTRY)
    v._link = None

    assert v._handle_citation_click(0, 5.0, 5.0) is False
    assert v.jumped_to == [] and idle == []


# ---- the popover itself ---------------------------------------------

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

needs_display = pytest.mark.skipif(not _display_ok,
                                   reason="no display for GTK tests")


class _PopoverHost:
    """The real popover builder, on a stand-in with a fake toolbar
    button: popping up a real MenuButton that was never realised
    aborts GTK."""

    _show_reference_popover = viewer.PdfViewerWindow._show_reference_popover

    def __init__(self):
        self._bibliography_by_n = ENTRY
        self._reference_popover = None
        self._jump_stack = []
        self.jumped_to = []
        self.popover = None
        host = self
        self.ref_btn = types.SimpleNamespace(
            set_label=lambda *_a: None, set_visible=lambda *_a: None,
            popup=lambda: None,
            set_popover=lambda pop: setattr(host, "popover", pop))

    def _ensure_bibliography_parsed(self):
        pass

    def _resolve_reference_and_render(self, *args):
        pass          # no network in a unit test

    def _push_jump_origin(self):
        self._jump_stack.append((0, 700.0))

    def _jump_to(self, page, top=None):
        self.jumped_to.append((page, top))

    def _on_popover_back(self, _btn):
        pass


def _buttons(widget):
    found = {}

    def walk(w):
        if isinstance(w, Gtk.Button) and w.get_label():
            found[w.get_label()] = w
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()

    walk(widget)
    return found


@needs_display
def test_in_place_offers_the_trip_and_hides_back_until_it_is_taken():
    host = _PopoverHost()
    host._show_reference_popover(12, TARGET_PAGE, TARGET_TOP, None, False)
    buttons = _buttons(host.popover.get_child())

    assert "Go to reference" in buttons
    back = buttons["Back to text"]
    assert back.get_visible() is False, \
        "nothing to go back to before any trip"

    buttons["Go to reference"].emit("clicked")

    assert host.jumped_to == [(TARGET_PAGE, TARGET_TOP)]
    assert back.get_visible() is True
    assert back.get_sensitive() is True


@needs_display
def test_after_a_fallback_jump_there_is_no_second_trip_offered():
    """The reader is already standing on the entry."""
    host = _PopoverHost()
    host._jump_stack.append((0, 700.0))          # the jump that happened
    host._show_reference_popover(12, TARGET_PAGE, TARGET_TOP, None, True)
    buttons = _buttons(host.popover.get_child())

    assert "Go to reference" not in buttons
    assert buttons["Back to text"].get_visible() is True
    assert buttons["Back to text"].get_sensitive() is True
