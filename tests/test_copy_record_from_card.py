"""Copy one paper as BibTeX or RIS from its card.

There was no way to get the BibTeX for a single paper. Both formats
could already render one — `sidecar_to_bibtex_record` and
`sidecar_to_ris_lines` take a single paper and nothing about them
was list-only — but the only callers were the whole-library exports
behind a save dialog.

The card's right-click menu offered prose citation styles (APA,
Vancouver, …) and nothing else, because BibTeX is not a CSL style
and lives in another module with a different shape.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

browse = pytest.importorskip("alexandria.browse")
from alexandria import sidecar


@pytest.fixture
def paper(tmp_path):
    pdf = str(tmp_path / "paper.pdf")
    with open(pdf, "wb") as fh:
        fh.write(b"%PDF-1.4 x")
    rec = sidecar.new_record(pdf)
    rec.update({"title": "Metabolic Engineering of Yeast",
                "authors": ["Shuobo Shi", "Yu Chen", "Jens Nielsen"],
                "year": 2025, "journal": "Annual Review of Biophysics",
                "doi": "10.1146/annurev-biophys-070924-103134",
                "volume": "54", "issue": "1", "pages": "101-120",
                "bibtex_key": "shi2025metabolic"})
    sc = sidecar.sidecar_path_for(pdf)
    sidecar.write(sc, rec)
    return {"pdf_path": pdf, "sidecar_path": sc}


class FakeWindow:
    def __init__(self):
        self.copied = None
        self.toasts = []

    def get_clipboard(self):
        window = self

        class Clip:
            def set(self, text):
                window.copied = text
        return Clip()

    def _toast(self, message, timeout=3):
        self.toasts.append(message)


class FakePopover:
    def popdown(self):
        pass


def test_bibtex_is_one_entry_for_this_paper(paper):
    win = FakeWindow()

    browse._do_copy_record("bibtex", "BibTeX", paper, win, FakePopover())

    assert win.copied.count("@") == 1
    assert "@article{shi2025metabolic," in win.copied
    assert "Metabolic Engineering of Yeast" in win.copied
    assert win.toasts == ["Copied as BibTeX"]


def test_bibtex_carries_the_biblio_fields(paper):
    """volume/issue/pages were the point of a7b16d2; a per-card copy
    that dropped them would be worse than the file export."""
    win = FakeWindow()

    browse._do_copy_record("bibtex", "BibTeX", paper, win, FakePopover())

    assert "volume  = {54}" in win.copied
    assert "number  = {1}" in win.copied
    assert "pages   = {101--120}" in win.copied     # en-dash range


def test_ris_is_one_record(paper):
    win = FakeWindow()

    browse._do_copy_record("ris", "RIS", paper, win, FakePopover())

    assert win.copied.startswith("TY  - ")
    assert win.copied.count("TY  - ") == 1
    assert "AU  - Shi, Shuobo" in win.copied
    assert win.toasts == ["Copied as RIS"]


def test_an_unreadable_sidecar_says_so_and_copies_nothing(tmp_path):
    win = FakeWindow()
    row = {"pdf_path": "/x/y.pdf",
           "sidecar_path": str(tmp_path / "missing.alexandria")}

    browse._do_copy_record("bibtex", "BibTeX", row, win, FakePopover())

    assert win.copied is None
    assert win.toasts and "Could not read sidecar" in win.toasts[0]


def test_the_clipboard_is_not_cleared_when_there_is_nothing_to_copy(
        tmp_path):
    """The failure mode that made a Chicago crash look like the wrong
    style: leaving stale clipboard content is confusing, but writing
    an empty string over the user's clipboard is worse."""
    pdf = str(tmp_path / "empty.pdf")
    with open(pdf, "wb") as fh:
        fh.write(b"%PDF-1.4")
    rec = sidecar.new_record(pdf)
    sc = sidecar.sidecar_path_for(pdf)
    sidecar.write(sc, rec)
    win = FakeWindow()

    browse._do_copy_record(
        "ris", "RIS", {"pdf_path": pdf, "sidecar_path": sc}, win,
        FakePopover())

    assert win.copied is None or win.copied.strip()


# --- the menu itself --------------------------------------------------

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


@pytest.mark.skipif(not _display_ok, reason="no display for GTK tests")
def test_the_menu_separates_styles_from_formats(paper):
    """"Cite this paper as…" lists prose styles; BibTeX and RIS are
    data formats. Putting them in one list would be a category
    error, so the popover carries two headings."""
    anchor = Gtk.Box()
    gesture = types.SimpleNamespace(get_widget=lambda: anchor)
    win = FakeWindow()
    # popup() on a popover whose parent is not realised aborts GTK,
    # and showing it is not what is under test.
    built = {}
    real_set_child = Gtk.Popover.set_child

    def capture(pop, child):
        built["child"] = child
        return real_set_child(pop, child)

    monkeypatch_popup = getattr(Gtk.Popover, "popup")
    Gtk.Popover.set_child = capture
    Gtk.Popover.popup = lambda self: None
    try:
        browse._show_cite_menu(gesture, 0, 0, paper, win)
    finally:
        Gtk.Popover.set_child = real_set_child
        Gtk.Popover.popup = monkeypatch_popup

    labels, headings = [], []
    child = built["child"].get_first_child()
    while child is not None:
        if isinstance(child, Gtk.Button):
            labels.append(child.get_label())
        elif isinstance(child, Gtk.Label):
            headings.append(child.get_text())
        child = child.get_next_sibling()

    assert "BibTeX" in labels and "RIS" in labels
    assert any("Cite this paper as" in h for h in headings)
    assert any("Extract as" in h for h in headings)
