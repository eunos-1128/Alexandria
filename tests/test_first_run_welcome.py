"""A first run asks where the library should live.

From the Flathub review (CodedOre, 2026-09-23): granting
`xdg-documents/Alexandria` forces that folder to exist "even if users
would want a different folder instead", and the suggestion was to let
the user choose on first start through the FileChooser portal.

The answer here is a welcome page rather than a dialog. A modal
asking where to keep files, before the user has seen the application,
is a question they cannot yet evaluate; and one dismissed by accident
is a decision lost, where a page is still there. It asks about *one*
folder, not "catalogues" — nobody knows what a catalogue is on their
first run — and the default is one click away.

Two things are tested: the flag that decides whether to ask, which
must never fire for someone who has been using Alexandria for months,
and the page itself.
"""

import json
import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import prefs

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False


# ---- when to ask -----------------------------------------------------

def test_a_first_run_has_not_chosen(tmp_path):
    path = str(tmp_path / "config.json")
    assert prefs.ensure_config_file(path) is True

    assert prefs.library_root_chosen(path) is False
    # ...and a default root is written all the same, so every code
    # path has somewhere to point while the user decides.
    with open(path) as fh:
        assert json.load(fh)["catalogues"][0]["library_root"]


def test_an_existing_config_is_never_asked(tmp_path):
    """The flag is absent from every config written before the
    welcome page existed. Those belong to people already using
    Alexandria, and greeting them with a first-run question would be
    absurd."""
    path = str(tmp_path / "config.json")
    prefs.save({"contact_email": "me@example.org",
                "catalogues": [{"name": "default",
                                "library_root": "/home/x/Papers"}]}, path)

    assert prefs.library_root_chosen(path) is True


def test_no_config_at_all_is_not_a_first_run(tmp_path):
    """`load` defaults everything in memory, so a missing file must
    not read as "unanswered" — `ensure_config_file` runs first and is
    what puts the question there."""
    assert prefs.library_root_chosen(str(tmp_path / "nothing.json")) is True


def test_answering_is_recorded(tmp_path):
    path = str(tmp_path / "config.json")
    prefs.ensure_config_file(path)

    prefs.mark_library_root_chosen(path)

    assert prefs.library_root_chosen(path) is True
    # and nothing else was disturbed
    assert prefs.load(path)["contact_email"] == ""


def test_the_answer_survives_a_reload(tmp_path):
    path = str(tmp_path / "config.json")
    prefs.ensure_config_file(path)
    prefs.mark_library_root_chosen(path)

    prefs.save(dict(prefs.load(path), sort_key="year"), path)

    assert prefs.library_root_chosen(path) is True


# ---- the page --------------------------------------------------------

pytestmark_gtk = pytest.mark.skipif(not _display_ok,
                                    reason="no display for GTK tests")


class _Window:
    """The welcome-page methods on a stand-in, so the page is built
    without a database, a watcher or a window."""

    if _display_ok:
        from alexandria.browse import BrowserWindow
        _build_welcome_page = BrowserWindow._build_welcome_page
        _on_welcome_use_default = BrowserWindow._on_welcome_use_default
        _on_welcome_choose_other = BrowserWindow._on_welcome_choose_other
        _finish_welcome = BrowserWindow._finish_welcome

    def __init__(self, library_root):
        self.library_root = library_root
        self.status = types.SimpleNamespace(
            set_text=lambda t: self.said.append(t))
        self.said = []
        self.reloaded = 0
        self.shown = None
        self._content_stack = types.SimpleNamespace(
            set_visible_child_name=lambda n: setattr(self, "shown", n))

    def _reload(self, _q):
        self.reloaded += 1


def _buttons(widget):
    out = []

    def walk(w):
        if isinstance(w, Gtk.Button):
            out.append(w)
        c = w.get_first_child()
        while c is not None:
            walk(c)
            c = c.get_next_sibling()

    walk(widget)
    return out


@pytestmark_gtk
def test_the_page_names_the_folder_it_proposes(tmp_path):
    root = str(tmp_path / "Documents" / "Alexandria")
    page = _Window(root)._build_welcome_page()

    assert isinstance(page, Adw.StatusPage)
    assert page.get_title() == "Welcome to Alexandria"
    assert root in page.get_description()


@pytestmark_gtk
def test_it_offers_the_default_and_an_alternative(tmp_path):
    page = _Window(str(tmp_path / "Alexandria"))._build_welcome_page()
    labels = [b.get_label() for b in _buttons(page)]

    assert labels == ["Use This Folder", "Choose Another Folder…"]


@pytestmark_gtk
def test_the_default_is_the_suggested_action(tmp_path):
    """One click for the person who doesn't care."""
    page = _Window(str(tmp_path / "Alexandria"))._build_welcome_page()
    use_btn = _buttons(page)[0]

    assert use_btn.has_css_class("suggested-action")


@pytestmark_gtk
def test_accepting_the_default_creates_the_folder_and_moves_on(
        tmp_path, monkeypatch):
    root = str(tmp_path / "Documents" / "Alexandria")
    win = _Window(root)
    marked = []
    monkeypatch.setattr(prefs, "mark_library_root_chosen",
                        lambda *a: marked.append(True))

    win._on_welcome_use_default(None)

    assert os.path.isdir(root), "the folder is made, not just recorded"
    assert marked == [True]
    assert win.shown == "library"
    assert win.reloaded == 1


@pytestmark_gtk
def test_a_folder_that_cannot_be_made_leaves_the_page_up(
        tmp_path, monkeypatch):
    """Nothing is recorded, so the question is asked again rather
    than the user being dropped into a library that does not exist."""
    win = _Window(str(tmp_path / "Alexandria"))
    marked = []
    monkeypatch.setattr(prefs, "mark_library_root_chosen",
                        lambda *a: marked.append(True))
    monkeypatch.setattr(os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("read-only file system")))

    win._on_welcome_use_default(None)

    assert marked == []
    assert win.shown is None
    assert any("read-only" in s for s in win.said)


@pytestmark_gtk
def test_a_failure_to_record_still_shows_the_library(tmp_path, monkeypatch):
    """Worst case is being asked again next launch — better than
    refusing to start."""
    win = _Window(str(tmp_path / "Alexandria"))
    monkeypatch.setattr(prefs, "mark_library_root_chosen",
                        lambda *a: (_ for _ in ()).throw(OSError("nope")))

    win._finish_welcome()

    assert win.shown == "library"


@pytestmark_gtk
def test_the_sandbox_explains_itself_on_the_page(tmp_path, monkeypatch):
    """Under Flatpak the choice is not a preference but what the
    application will be allowed to open, so the page says so."""
    from alexandria import browse, sandbox

    monkeypatch.setattr(
        sandbox, "access_summary",
        lambda *a, **k: "This Flatpak can only open files in ~/Documents.")
    page = _Window(str(tmp_path / "Alexandria"))._build_welcome_page()

    assert "can only open files in ~/Documents" in page.get_description()
