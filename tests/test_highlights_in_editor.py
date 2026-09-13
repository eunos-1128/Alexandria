"""Highlights, shown in the metadata editor but not editable there.

Asked for 2026-09-13. Highlights and their comments were written by
the viewer, stored in the sidecar and listed in the viewer's sidebar —
and were invisible from the editor, which is where someone goes to ask
what a paper has on it. Read-only by intent: a highlight is anchored to
quads on a page, and a text box here could not say which words on which
page an edit should re-anchor to. The question this answers is "is
there anything marked, and what did I say about it".
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

from alexandria import edit_dialog


def h(page, text, comment="", author="", top=0.0):
    return {"id": "x", "page": page, "quads": [[10.0, top, 100.0, 12.0]],
            "text": text, "color": "yellow", "comment": comment,
            "author": author, "created": "2026-09-13T10:00:00",
            "modified": "2026-09-13T10:00:00"}


def test_text_and_comment_both_appear():
    out = edit_dialog.highlights_text([
        h(2, "dual-space direct methods", "this is the key claim",
          author="Paul")])

    assert "p.3" in out                       # stored 0-based, shown 1-based
    assert "dual-space direct methods" in out
    assert "this is the key claim" in out
    assert "(Paul)" in out


def test_a_highlight_without_a_comment_is_just_the_quote():
    out = edit_dialog.highlights_text([h(0, "peak picking in real space")])

    assert out.count("\n") == 0
    assert "peak picking in real space" in out


def test_reading_order_not_creation_order():
    """Someone checking what they marked reads it the way they read the
    paper."""
    out = edit_dialog.highlights_text([
        h(5, "last", top=100.0),
        h(1, "middle of page two", top=400.0),
        h(1, "top of page two", top=90.0),
    ])

    assert out.index("top of page two") < out.index("middle of page two")
    assert out.index("middle of page two") < out.index("last")


def test_whitespace_from_a_pdf_selection_is_collapsed():
    """Selected PDF text arrives with the line breaks of the column it
    came from, which would make one highlight eight lines tall."""
    out = edit_dialog.highlights_text([
        h(0, "Truncation of the data\nat a particular\n resolution")])

    assert "Truncation of the data at a particular resolution" in out


def test_a_malformed_entry_cannot_break_the_editor():
    """This runs while opening a dialog; a bad sidecar must not stop it
    opening."""
    out = edit_dialog.highlights_text([
        "not a dict",
        {"page": "nonsense", "text": "kept anyway"},
        {"page": 0, "quads": "rubbish", "text": "also kept"},
        h(0, "normal one"),
    ])

    assert "kept anyway" in out
    assert "also kept" in out
    assert "normal one" in out


def test_a_highlight_with_no_text_still_says_it_is_there():
    """A highlight over a figure captures no text, and hiding it would
    understate what is marked on the paper."""
    out = edit_dialog.highlights_text([h(3, "", comment="the SAD map")])

    assert "p.4" in out
    assert "no text captured" in out
    assert "the SAD map" in out


def test_nothing_at_all_when_there_are_none():
    """The dialog is long already; an empty box earns no space."""
    assert edit_dialog.highlights_text([]) == ""
    assert edit_dialog.highlights_text(None) == ""


def _views(widget, found=None):
    """Every Gtk.TextView under `widget`."""
    found = [] if found is None else found
    if isinstance(widget, Gtk.TextView):
        found.append(widget)
    child = widget.get_first_child() if hasattr(widget, "get_first_child") \
        else None
    while child is not None:
        _views(child, found)
        child = child.get_next_sibling()
    return found


def _text_of(view):
    buf = view.get_buffer()
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


@pytest.mark.skipif(not _display_ok, reason="no display for GTK tests")
def test_the_editor_shows_them_and_will_not_let_them_be_edited(tmp_path):
    from alexandria import index, sidecar

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    rec = sidecar.new_record(str(pdf))
    rec["title"] = "Substructure solution with SHELXD"
    rec["highlights"] = [h(2, "dual-space direct methods", "the key claim")]
    sc = sidecar.sidecar_path_for(str(pdf))
    sidecar.write(sc, rec)
    conn = index.open_db(str(tmp_path / "lib.db"))

    win = None
    try:
        edit_dialog.open_editor(None, conn, str(pdf), sc, lambda: None)
        # The editor is the only window it made.
        win = [w for w in Gtk.Window.get_toplevels()
               if w.get_title() and w.get_title().startswith("Edit:")][-1]
        shown = [v for v in _views(win)
                 if "dual-space direct methods" in _text_of(v)]
        assert shown, "the highlight is not on the dialog"
        view = shown[0]
        assert view.get_editable() is False
        assert view.get_sensitive() is True, \
            "greying it out would stop the text being copied"
        assert "the key claim" in _text_of(view)
    finally:
        if win is not None:
            win.destroy()


@pytest.mark.skipif(not _display_ok, reason="no display for GTK tests")
def test_a_paper_with_no_highlights_gets_no_box(tmp_path):
    from alexandria import index, sidecar

    pdf = tmp_path / "bare.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    rec = sidecar.new_record(str(pdf))
    sc = sidecar.sidecar_path_for(str(pdf))
    sidecar.write(sc, rec)
    conn = index.open_db(str(tmp_path / "lib.db"))

    win = None
    try:
        edit_dialog.open_editor(None, conn, str(pdf), sc, lambda: None)
        win = [w for w in Gtk.Window.get_toplevels()
               if w.get_title() and w.get_title().startswith("Edit:")][-1]
        labels = []

        def walk(w):
            if isinstance(w, Gtk.Label):
                labels.append(w.get_text())
            c = w.get_first_child()
            while c is not None:
                walk(c)
                c = c.get_next_sibling()

        walk(win)
        assert not any("Highlights" in (t or "") for t in labels)
    finally:
        if win is not None:
            win.destroy()
