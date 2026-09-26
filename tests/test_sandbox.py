"""Knowing we are in a Flatpak, and what it lets us open.

The Flathub review of Alexandria turned on this: the manifest grants
`xdg-documents/Alexandria`, so a PDF dragged from anywhere else in
`~/Documents` is invisible to the application — and the drop handler
reported it as "no PDFs found", which blames the file.

These tests drive `sandbox` off fixture `/.flatpak-info` files rather
than the real one, so they run the same inside and outside a sandbox.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import sandbox

HOME = os.path.expanduser("~")

# What `flatpak info --show-permissions io.github.pemsley.Alexandria`
# printed on 2026-09-25, in the file's own ini form.
ALEXANDRIA_INFO = """\
[Application]
name=io.github.pemsley.Alexandria
runtime=runtime/org.gnome.Platform/x86_64/49

[Instance]
flatpak-version=1.16.1

[Context]
shared=ipc;network;
sockets=fallback-x11;wayland;
devices=dri;
filesystems=xdg-documents/Alexandria:create;
"""


@pytest.fixture(autouse=True)
def _fresh_cache():
    sandbox.forget_info()
    yield
    sandbox.forget_info()


@pytest.fixture
def info(tmp_path):
    """Write a `/.flatpak-info` and return its path."""
    def write(text=ALEXANDRIA_INFO):
        p = tmp_path / "flatpak-info"
        p.write_text(text)
        return str(p)
    return write


@pytest.fixture(autouse=True)
def _stable_xdg(monkeypatch):
    """Pin the XDG lookups: a test machine's Documents folder may be
    localised or relocated, and that is not what is under test."""
    monkeypatch.setattr(sandbox, "_xdg_special",
                        lambda name: os.path.join(HOME, name.capitalize()))


# ---- detection -------------------------------------------------------

def test_no_flatpak_info_means_no_sandbox(tmp_path):
    missing = str(tmp_path / "nothing-here")

    assert sandbox.in_flatpak(missing) is False
    assert sandbox.app_id(missing) == ""
    assert sandbox.granted_filesystems(missing) == ()


def test_the_file_is_the_marker_and_the_configuration(info):
    p = info()

    assert sandbox.in_flatpak(p) is True
    assert sandbox.app_id(p) == "io.github.pemsley.Alexandria"
    assert sandbox.granted_filesystems(p) == (
        "xdg-documents/Alexandria:create",)


def test_outside_a_sandbox_everything_is_accessible(tmp_path):
    missing = str(tmp_path / "nothing-here")

    assert sandbox.can_access("/etc/passwd", missing) is True
    assert sandbox.can_access(os.path.join(HOME, "anywhere.pdf"),
                              missing) is True
    assert sandbox.access_summary(missing) == ""


# ---- what the grant expands to ---------------------------------------

def test_the_alexandria_grant_is_one_folder(info):
    p = info()

    assert sandbox.granted_paths(p) == [
        os.path.join(HOME, "Documents", "Alexandria")]


@pytest.mark.parametrize("token,expected", [
    ("home", HOME),
    ("host", "/"),
    ("xdg-documents", os.path.join(HOME, "Documents")),
    ("xdg-download:ro", os.path.join(HOME, "Download")),
    ("xdg-documents/Alexandria:create",
     os.path.join(HOME, "Documents", "Alexandria")),
    ("~/Papers", os.path.join(HOME, "Papers")),
    ("/srv/shared:rw", "/srv/shared"),
    ("xdg-config/Alexandria", os.path.join(HOME, ".config", "Alexandria")),
    ("xdg-data", os.path.join(HOME, ".local", "share")),
])
def test_token_spellings(token, expected):
    assert sandbox._expand_token(token) == expected


@pytest.mark.parametrize("token", [
    "host-os", "host-etc", "!home", "", "   ", "something-invented",
])
def test_tokens_that_name_no_user_path(token):
    assert sandbox._expand_token(token) is None


# ---- the question the UI actually asks --------------------------------

def test_a_file_inside_the_grant_is_ours_to_open(info):
    p = info()
    inside = os.path.join(HOME, "Documents", "Alexandria", "paper.pdf")

    assert sandbox.can_access(inside, p) is True


def test_a_file_one_level_up_is_not(info):
    """The reported case: dragged from ~/Documents, not from the
    library folder."""
    p = info()
    outside = os.path.join(HOME, "Documents", "paper.pdf")

    assert sandbox.can_access(outside, p) is False


def test_permission_is_not_existence(info):
    """A granted path that does not exist still answers True — that
    is the whole point: it separates "not there" from "not mine"."""
    p = info()
    absent = os.path.join(HOME, "Documents", "Alexandria", "nope.pdf")

    assert not os.path.exists(absent)
    assert sandbox.can_access(absent, p) is True


def test_a_sibling_prefix_is_not_inside(info):
    """String prefixes are not path prefixes: `.../AlexandriaOld` must
    not pass because it starts with `.../Alexandria`."""
    p = info()
    sibling = os.path.join(HOME, "Documents", "AlexandriaOld", "x.pdf")

    assert sandbox.can_access(sibling, p) is False


def test_the_apps_own_directories_are_always_reachable(info):
    """Inside the sandbox these are redirected to ~/.var/app/<id>/,
    and the application must not conclude it cannot read its own
    config."""
    p = info()

    assert sandbox.can_access(
        os.path.join(HOME, ".config", "Alexandria", "config.json"), p) is True
    assert sandbox.can_access(
        os.path.join(HOME, ".local", "state", "Alexandria", "lib.db"),
        p) is True


def test_portal_granted_files_are_reachable(info, monkeypatch):
    """A folder chosen through the FileChooser portal appears under
    the runtime directory, granted dynamically and named nowhere in
    the manifest."""
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    p = info()

    assert sandbox.can_access("/run/user/1000/doc/a1b2c3/Papers/x.pdf",
                              p) is True


def test_host_access_permits_everything(info):
    p = info(ALEXANDRIA_INFO.replace(
        "filesystems=xdg-documents/Alexandria:create;", "filesystems=host;"))

    assert sandbox.can_access("/etc/passwd", p) is True
    assert sandbox.access_summary(p) == "", "nothing to warn about"


# ---- the sentence the user sees --------------------------------------

def test_the_summary_names_the_folder_in_tilde_form(info):
    p = info()

    assert sandbox.access_summary(p) == (
        "This Flatpak can only open files in ~/Documents/Alexandria.")


def test_the_summary_lists_several(info):
    p = info(ALEXANDRIA_INFO.replace(
        "filesystems=xdg-documents/Alexandria:create;",
        "filesystems=xdg-documents:create;xdg-download:ro;/srv/papers;"))

    assert sandbox.access_summary(p) == (
        "This Flatpak can only open files in ~/Documents, ~/Download "
        "and /srv/papers.")


def test_a_sandbox_granted_nothing_says_so(info):
    p = info(ALEXANDRIA_INFO.replace(
        "filesystems=xdg-documents/Alexandria:create;", "filesystems=;"))

    assert sandbox.granted_paths(p) == []
    assert "not been granted access" in sandbox.access_summary(p)


# ---- paths dressed as paths ------------------------------------------
#
# A folder name in the middle of a sentence reads as prose. In a
# monospace face it reads as a thing on disk, which is what it is.

def test_a_path_is_set_in_tt():
    assert sandbox.as_path_markup("/home/paule/Documents/Alexandria") == (
        "<tt>/home/paule/Documents/Alexandria</tt>")


def test_a_path_with_markup_characters_is_escaped():
    """Folder names are the user's, not ours. An unescaped & would
    take the whole label down with it."""
    out = sandbox.as_path_markup("~/Papers & <notes>")

    assert out == "<tt>~/Papers &amp; &lt;notes&gt;</tt>"

    from alexandria.markup import _markup_parses
    assert _markup_parses(out)


def test_the_summary_can_be_asked_for_markup(info):
    p = info()

    assert sandbox.access_summary(p, markup=True) == (
        "This Flatpak can only open files in "
        "<tt>~/Documents/Alexandria</tt>.")


def test_every_path_in_a_list_is_marked_up(info):
    p = info(ALEXANDRIA_INFO.replace(
        "filesystems=xdg-documents/Alexandria:create;",
        "filesystems=xdg-documents:create;/srv/papers;"))

    assert sandbox.access_summary(p, markup=True) == (
        "This Flatpak can only open files in <tt>~/Documents</tt> "
        "and <tt>/srv/papers</tt>.")


def test_plain_text_is_still_the_default(info):
    """Toasts do not render Pango: a <tt> there would be shown."""
    assert "<tt>" not in sandbox.access_summary(info())


# ---- the message the drop handler gives ------------------------------
#
# The symptom that started all this: a PDF dragged from ~/Documents
# into the Flatpak reported "Drop: no PDFs found", which blames the
# file for a permission the packaging never granted.

import types

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

needs_gtk = pytest.mark.skipif(not _display_ok,
                               reason="no display for GTK tests")


class _Dropped:
    """Stands in for the Gdk.FileList a drop delivers."""

    def __init__(self, *paths):
        self._files = [types.SimpleNamespace(get_path=lambda p=p: p)
                       for p in paths]

    def get_files(self):
        return self._files


class _Win:
    def __init__(self):
        self.toasts = []
        self.status = types.SimpleNamespace(set_text=lambda _t: None)

    def _toast(self, text):
        self.toasts.append(text)


@needs_gtk
def test_an_unreachable_pdf_is_not_reported_as_missing(monkeypatch):
    from alexandria import browse

    monkeypatch.setattr(sandbox, "can_access", lambda *a, **k: False)
    monkeypatch.setattr(
        sandbox, "access_summary",
        lambda *a, **k: "This Flatpak can only open files in ~/Documents/Alexandria.")
    win = _Win()

    handled = browse.BrowserWindow._on_drop(
        win, None, _Dropped("/home/paule/Documents/elsewhere.pdf"), 0, 0)

    assert handled is False
    assert len(win.toasts) == 1
    assert "Can't open elsewhere.pdf" in win.toasts[0]
    assert "~/Documents/Alexandria" in win.toasts[0]


@needs_gtk
def test_a_genuinely_absent_pdf_still_says_so(monkeypatch):
    """Outside a sandbox, or inside one where the path *is* granted,
    the old message is the right one."""
    from alexandria import browse

    monkeypatch.setattr(sandbox, "can_access", lambda *a, **k: True)
    win = _Win()

    handled = browse.BrowserWindow._on_drop(
        win, None, _Dropped("/home/paule/Documents/Alexandria/gone.pdf"),
        0, 0)

    assert handled is False
    assert win.toasts == ["Drop: no PDFs found"]


@needs_gtk
def test_a_non_pdf_is_not_a_permission_problem(monkeypatch):
    from alexandria import browse

    monkeypatch.setattr(sandbox, "can_access", lambda *a, **k: False)
    win = _Win()

    browse.BrowserWindow._on_drop(win, None, _Dropped("/tmp/notes.txt"), 0, 0)

    assert win.toasts == ["Drop: no PDFs found"]
