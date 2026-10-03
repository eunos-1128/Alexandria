"""The summary popover shows a TextView, built when it opens.

`summary_view.make_summary_view` renders into a Gtk.TextView with
TextTags; the popover used to hold a Gtk.Label fed by
`markup.markdown_to_pango`. A label has no paragraph spacing, no
margins and no full-width backgrounds, so this is the widget swap that
lets the renderer show what it can do.

Two things about *when* it is built, both load-bearing:

  * **Not at card-build time.** `_is_dark_theme` reads a realized,
    styled widget's resolved foreground. Nothing in a card being built
    is realized, and an unrealized widget answers with a default
    colour rather than raising — so the fallback inside
    `_is_dark_theme` would never fire and every summary would be
    styled for the light theme. Building on the popover's `map` gives
    it a mapped widget to ask.
  * **Not on every open.** A library reload builds every card's
    popover, and parsing Markdown for summaries nobody opens is work
    thrown away.

These drive `_fill_summary_body` directly rather than popping a real
popover up. An earlier version of this file did the latter — present a
window, `popup()`, pump the main loop — and passed two runs in three:
a popover whose parent window has not finished mapping silently fails
to appear, and a window left open by a previous test makes the next
one fail the same way ("Tried to map a grabbing popup with a non-top
most parent"). Emitting `map` by hand instead aborts the process
outright, GTK's own handler running against a widget that was never
realized. The signal wiring is checked on its own, which is the part
that genuinely needs GTK.
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
    from gi.repository import GObject, Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(not _display_ok,
                                reason="no display for GTK tests")

from alexandria import browse

SUMMARY = {"text": "# Findings\n\nThe **GOLD** docking paper, with "
                   "*italics* and `code`.\n\n- a point\n- another\n",
           "model": "claude-opus-5", "source": "jats",
           "generated_at": "2026-10-03"}


def _descendants(widget):
    out = [widget]
    child = widget.get_first_child()
    while child is not None:
        out.extend(_descendants(child))
        child = child.get_next_sibling()
    return out


def _of_type(widget, kind):
    return [w for w in _descendants(widget) if isinstance(w, kind)]


def _scroller(pop):
    return _of_type(pop, Gtk.ScrolledWindow)[0]


def _open(pop, built=None, text=None):
    """What the popover's `map` handler does, without the popover
    having to map."""
    browse._fill_summary_body(
        pop, _scroller(pop),
        SUMMARY["text"] if text is None else text,
        {"dark": None} if built is None else built)


def _view(pop):
    return _of_type(pop, Gtk.TextView)[0]


def _body_text(view):
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def _tags_used(view):
    buf = view.get_buffer()
    names, it = set(), buf.get_start_iter()
    while True:
        for tag in it.get_tags():
            name = tag.get_property("name")
            if name:
                names.add(name)
        if not it.forward_to_tag_toggle(None):
            return names


def _map_handler(pop):
    return GObject.signal_handler_find(
        pop, GObject.SignalMatchType.ID,
        GObject.signal_lookup("map", Gtk.Widget), 0, None, None, None)


# ---- before it opens -------------------------------------------------

def test_the_body_is_not_built_with_the_card():
    """The popover exists; the rendering has not happened yet."""
    pop = browse.make_summary_chip(SUMMARY).get_popover()

    assert pop is not None
    assert _of_type(pop, Gtk.TextView) == []
    assert _scroller(pop).get_child() is None


def test_something_is_connected_to_map():
    """The half that has to be GTK: the build is wired to the signal
    rather than left for nobody to call."""
    pop = browse.make_summary_chip(SUMMARY).get_popover()

    assert _map_handler(pop) != 0


def test_the_chip_still_says_who_wrote_it():
    chip = browse.make_summary_chip(SUMMARY)

    assert "claude-opus-5" in (chip.get_tooltip_text() or "")


# ---- opening it ------------------------------------------------------

def test_opening_it_builds_a_textview():
    pop = browse.make_summary_chip(SUMMARY).get_popover()

    _open(pop)

    assert len(_of_type(pop, Gtk.TextView)) == 1
    assert _of_type(pop, Gtk.Label), "the heading label is still a Label"


def test_the_markdown_is_rendered_not_shown():
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    _open(pop)

    text = _body_text(_view(pop))

    assert text.startswith("Findings"), "the # is consumed"
    assert "**" not in text and "`" not in text
    assert "GOLD" in text


def test_the_rendering_uses_tags():
    """The point of the swap: a Label could not carry these."""
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    _open(pop)

    used = _tags_used(_view(pop))

    assert "h1" in used
    assert "bold" in used and "italic" in used
    assert "inline-code" in used
    assert any(t.startswith("list-item") for t in used)


def test_the_body_is_read_only_but_selectable():
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    _open(pop)

    view = _view(pop)

    assert view.get_editable() is False
    assert view.get_cursor_visible() is False
    assert view.get_wrap_mode() == Gtk.WrapMode.WORD_CHAR


def test_opening_it_twice_does_not_rebuild():
    """A reload builds every card's popover; re-parsing on every open
    is the same waste on a timer."""
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    built = {"dark": None}
    _open(pop, built=built)
    first = _view(pop)

    _open(pop, built=built)

    assert _view(pop) is first


def test_a_theme_flip_rebuilds_it(monkeypatch):
    """Tag colours are baked into the buffer's tag table when the text
    is laid down, so a restyle is not enough."""
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    built = {"dark": None}
    monkeypatch.setattr(browse, "_is_dark_theme", lambda _w: False)
    _open(pop, built=built)
    light = _view(pop)

    monkeypatch.setattr(browse, "_is_dark_theme", lambda _w: True)
    _open(pop, built=built)

    assert _view(pop) is not light
    assert built["dark"] is True


def test_the_theme_is_read_from_the_popover(monkeypatch):
    """Not from the style manager: a GTK_THEME override (what
    `--light` uses for screen recording) moves the rendering without
    moving `Adw.StyleManager.get_dark()`."""
    asked = []
    monkeypatch.setattr(browse, "_is_dark_theme",
                        lambda w: asked.append(w) or False)
    pop = browse.make_summary_chip(SUMMARY).get_popover()

    _open(pop)

    assert asked == [pop]


def test_an_empty_summary_does_not_break_it():
    pop = browse.make_summary_chip({"text": "", "model": "m"}).get_popover()

    _open(pop, text="")

    assert _body_text(_view(pop)) == ""


# ---- without markdown-it-py ------------------------------------------

def test_the_label_still_renders_when_the_parser_is_absent(monkeypatch):
    """An installation that predates the dependency keeps a readable
    summary rather than a window that will not start: the renderer is
    imported in a try/except and the popover falls back here."""
    monkeypatch.setattr(browse, "_summary_renderer", lambda: None)
    pop = browse.make_summary_chip(SUMMARY).get_popover()

    _open(pop)

    assert _of_type(pop, Gtk.TextView) == []
    # GTK4 wraps a non-scrollable child in a Gtk.Viewport, so the
    # label is a grandchild of the scroller rather than its child.
    bodies = [w for w in _of_type(pop, Gtk.Label) if w.get_selectable()]
    assert len(bodies) == 1
    # The old renderer, with its subset: bold survives as markup.
    assert "<b>GOLD</b>" in bodies[0].get_label()


def test_the_fallback_label_is_built_once(monkeypatch):
    monkeypatch.setattr(browse, "_summary_renderer", lambda: None)
    pop = browse.make_summary_chip(SUMMARY).get_popover()
    built = {"dark": None}
    _open(pop, built=built)
    first = [w for w in _of_type(pop, Gtk.Label) if w.get_selectable()][0]

    _open(pop, built=built)

    assert [w for w in _of_type(pop, Gtk.Label)
            if w.get_selectable()][0] is first


def test_the_popover_is_wired_the_same_either_way(monkeypatch):
    """The fallback is chosen when the body is built, not when the
    card is: nothing can know whether the import will succeed without
    doing it, and doing it is what we are avoiding."""
    monkeypatch.setattr(browse, "_summary_renderer", lambda: None)

    pop = browse.make_summary_chip(SUMMARY).get_popover()

    assert _map_handler(pop) != 0
    assert _scroller(pop).get_child() is None


# ---- what importing browse costs -------------------------------------

def test_importing_browse_does_not_import_markdown_it():
    """markdown-it-py is ~19 ms of import time for a popover nobody
    may open. Run in a subprocess because this process has almost
    certainly imported it already, by rendering a summary above."""
    import subprocess
    import sys as _sys

    out = subprocess.run(
        [_sys.executable, "-c",
         "import sys, alexandria.browse;"
         " print('markdown_it' in sys.modules)"],
        cwd=ROOT, capture_output=True, text=True, timeout=120)

    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "False", out.stdout


def test_the_renderer_is_imported_once_and_remembered():
    first = browse._summary_renderer()

    assert first is not None
    assert browse._summary_renderer() is first


def test_a_missing_parser_is_not_retried_per_summary(monkeypatch):
    """`False` records "tried, and absent", so a missing dependency
    costs one failed import rather than one per card. (Faking the
    import itself does not work: `from . import summary_view` calls
    `__import__("alexandria", fromlist=...)`, and a module already in
    sys.modules is handed back without the machinery being consulted
    at all.)"""
    monkeypatch.setattr(browse, "_summary_view", False)

    assert browse._summary_renderer() is None
    assert browse._summary_renderer() is None
    assert browse._summary_view is False, "not reset, so never retried"
