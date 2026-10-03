"""The OpenAlex API key can be set in Preferences.

Until now only the contact address had a row; the key could be set by
editing `~/.config/Alexandria/config.json` by hand or via
`$ALEXANDRIA_OPENALEX_API_KEY`. Everything else was already in place —
`prefs.get/set_openalex_api_key`, and `metrics.set_openalex_api_key`
whose docstring says it is for a key "reloaded from prefs after the
user pastes one in".

Why it matters: without a key, requests draw on the pool every unkeyed
copy shares, and a first run meets
"OpenAlex credits at 977, below buffer 1500 — stopping refresher".
The fix for that was a config file the user had never heard of.

These stub `prefs.get/set_openalex_api_key` rather than letting the
dialog write: `prefs.load(path=DEFAULT_PATH)` binds its default
argument at definition, so monkeypatching `prefs.DEFAULT_PATH` would
*not* redirect it and the tests would edit the real config.
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
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(not _display_ok,
                                reason="no display for GTK tests")

from alexandria import browse, metrics, prefs


class _Window:
    """`_open_preferences` on a stand-in carrying only the handful of
    attributes it touches."""

    _open_preferences = browse.BrowserWindow._open_preferences
    _MARK_FALLBACK_NAMES = browse.BrowserWindow._MARK_FALLBACK_NAMES

    def __init__(self):
        self.library_root = "/tmp/library"
        self.mark_labels = {}
        self.status = types.SimpleNamespace(set_text=lambda _t: None)
        self.search = types.SimpleNamespace(get_text=lambda: "")

    def _apply_library_root(self, _p):
        return True

    def _refresh_mark_filter_dd(self):
        pass

    def _reload(self, _q=None):
        pass


@pytest.fixture
def prefs_ui(monkeypatch):
    """Every (group, row) the dialog adds, recorded as it is built.

    Not walked from the dialog widget: an `Adw.PreferencesDialog`
    builds no widget tree until it is presented, and presenting one
    needs a parent window on screen — `get_first_child()` on an
    unpresented dialog finds nothing at all, not even the row that was
    there before today."""
    added = []
    real_add = Adw.PreferencesGroup.add

    def record(group, row):
        added.append((group, row))
        return real_add(group, row)

    monkeypatch.setattr(Adw.PreferencesGroup, "add", record)
    monkeypatch.setattr(Adw.PreferencesDialog, "present",
                        lambda self, parent=None: None)
    _Window()._open_preferences(None)
    assert added, "the dialog added no rows at all"
    return added


@pytest.fixture(autouse=True)
def stored_key(monkeypatch):
    """A fake stored key, so no test reads or writes the real
    config.json."""
    box = {"key": "stored-key-123"}
    monkeypatch.setattr(prefs, "get_openalex_api_key", lambda: box["key"])
    monkeypatch.setattr(prefs, "set_openalex_api_key",
                        lambda k: box.__setitem__("key", k))
    monkeypatch.setattr(prefs, "get_contact_email", lambda *a, **k: "")
    monkeypatch.setattr(prefs, "set_contact_email", lambda *a, **k: None)
    return box


@pytest.fixture(autouse=True)
def no_env_key(monkeypatch):
    monkeypatch.delenv("ALEXANDRIA_OPENALEX_API_KEY", raising=False)


def _rows(added, kind):
    return [row for _group, row in added if isinstance(row, kind)]


def _key_row(added):
    rows = _rows(added, Adw.PasswordEntryRow)
    assert len(rows) == 1, "exactly one masked row"
    return rows[0]


def _group(added, title):
    groups = {g for g, _r in added if g.get_title() == title}
    assert len(groups) == 1, "one group titled %r" % title
    return groups.pop()


def _build(monkeypatch):
    """Build the dialog now, after a test has changed the
    environment — the fixture builds too early for that."""
    added = []
    real_add = Adw.PreferencesGroup.add
    monkeypatch.setattr(
        Adw.PreferencesGroup, "add",
        lambda g, r: (added.append((g, r)), real_add(g, r))[1])
    monkeypatch.setattr(Adw.PreferencesDialog, "present",
                        lambda self, parent=None: None)
    _Window()._open_preferences(None)
    return added


# ---- the row exists --------------------------------------------------

def test_there_is_a_row_for_the_key(prefs_ui):
    assert _key_row(prefs_ui).get_title() == "OpenAlex API key"


def test_it_is_masked(prefs_ui):
    """`Adw.PasswordEntryRow` is the platform's widget for a secret:
    it hides the text and offers a reveal button."""
    assert isinstance(_key_row(prefs_ui), Adw.PasswordEntryRow)


def test_it_shows_the_key_already_stored(prefs_ui):
    assert _key_row(prefs_ui).get_text() == "stored-key-123"


def test_the_email_row_is_still_there(prefs_ui):
    """A plain EntryRow, unchanged — an address is not a secret.

    Scoped to this group: the mark labels and the comment author are
    EntryRows too, and are none of this test's business."""
    net = _group(prefs_ui, "Online services")
    plain = [r for g, r in prefs_ui
             if g is net and isinstance(r, Adw.EntryRow)
             and not isinstance(r, Adw.PasswordEntryRow)]

    assert [r.get_title() for r in plain] == ["Contact email"]


def test_both_rows_are_in_the_online_services_group(prefs_ui):
    net = _group(prefs_ui, "Online services")

    titles = [r.get_title() for g, r in prefs_ui if g is net]

    assert titles == ["Contact email", "OpenAlex API key"]


def test_the_group_explains_what_a_key_buys(prefs_ui):
    said = _group(prefs_ui, "Online services").get_description()

    assert "openalex.org/settings/api" in said, "says where to get one"
    assert "free" in said
    assert "private daily budget" in said
    # ...and still explains the address, which is a different bargain.
    assert "Unpaywall" in said


# ---- typing in it ----------------------------------------------------

def test_typing_a_key_stores_it(prefs_ui, stored_key):
    _key_row(prefs_ui).set_text("new-key-abc")

    assert stored_key["key"] == "new-key-abc"


def test_it_takes_effect_without_a_restart(prefs_ui, monkeypatch):
    """`metrics` holds the key in a module global, read at import.
    Saving to the config alone would do nothing until next launch."""
    told = []
    monkeypatch.setattr(metrics, "set_openalex_api_key", told.append)

    _key_row(prefs_ui).set_text("new-key-abc")

    assert told[-1] == "new-key-abc"


def test_surrounding_space_is_dropped(prefs_ui, stored_key):
    """A key pasted from a web page brings whitespace with it."""
    _key_row(prefs_ui).set_text("  padded-key  ")

    assert stored_key["key"] == "padded-key"


def test_clearing_it_clears_the_stored_key(prefs_ui, stored_key):
    _key_row(prefs_ui).set_text("")

    assert stored_key["key"] == ""


# ---- when the environment overrides it -------------------------------

def test_an_env_var_disables_the_row(monkeypatch):
    """`metrics.set_openalex_api_key` returns early when the env var
    is set, so anything typed here would be saved and then ignored."""
    monkeypatch.setenv("ALEXANDRIA_OPENALEX_API_KEY", "from-the-env")

    row = _key_row(_build(monkeypatch))

    assert row.get_sensitive() is False
    assert "ALEXANDRIA_OPENALEX_API_KEY" in (row.get_tooltip_text() or "")


def test_an_env_var_means_nothing_is_written(monkeypatch, stored_key):
    monkeypatch.setenv("ALEXANDRIA_OPENALEX_API_KEY", "from-the-env")

    _key_row(_build(monkeypatch)).set_text("typed-anyway")

    assert stored_key["key"] == "stored-key-123", "no handler connected"
