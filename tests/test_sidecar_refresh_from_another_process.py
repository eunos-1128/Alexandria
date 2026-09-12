"""A summary written by the MCP server must appear without a restart.

The MCP server is a separate process. `set_summary` writes the
sidecar and upserts the index, so the database is right — but the
open window had no idea, and the summary chip only appeared after a
restart.

`sidecar.write` is tmp + `os.replace`, which the kernel surfaces as
RENAMED, and the watcher's RENAMED branch handled only PDFs. That
was defensible when the GUI was the only writer and had already
redrawn; it stopped being true the moment a second Alexandria
process could write.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import index, sidecar, watcher


@pytest.fixture
def library(tmp_path):
    root = str(tmp_path)
    db = os.path.join(root, "db.sqlite3")
    conn = index.open_db(db)
    pdf = os.path.join(root, "paper.pdf")
    with open(pdf, "wb") as fh:
        fh.write(b"%PDF-1.4 x")
    rec = sidecar.new_record(pdf)
    rec["title"] = "A paper"
    sc = sidecar.sidecar_path_for(pdf)
    sidecar.write(sc, rec)
    index.upsert(conn, pdf, sc, None, rec, os.path.getmtime(sc))
    conn.close()

    w = watcher.LibraryWatcher.__new__(watcher.LibraryWatcher)
    w.db_path = db
    w.root = root
    w._suppress = {}
    w._suppress_lock = __import__("threading").Lock()
    w.on_change = None
    return w, sc, root


def test_a_known_sidecar_triggers_a_reload(library, monkeypatch):
    w, sc, _root = library
    seen = []
    w.on_change = lambda status: seen.append(status)
    monkeypatch.setattr(watcher.GLib, "idle_add",
                        lambda fn, *a: fn(*a))

    w._do_sidecar_refresh(sc)

    assert seen == ["sidecar-refresh"]


def test_an_unknown_sidecar_is_ignored(library, monkeypatch):
    """A sidecar matching no paper is the emailed-sidecar case, which
    has its own handling — importing it blindly would clobber the
    recipient's notes."""
    w, _sc, root = library
    stray = os.path.join(root, "someone-elses.pdf.alexandria")
    with open(stray, "w") as fh:
        fh.write("{}")
    seen = []
    w.on_change = lambda status: seen.append(status)
    monkeypatch.setattr(watcher.GLib, "idle_add",
                        lambda fn, *a: fn(*a))

    w._do_sidecar_refresh(stray)

    assert seen == []


def test_our_own_writes_stay_suppressed(library, monkeypatch):
    """The GUI suppresses the path when it writes a sidecar itself;
    that must still silence the event."""
    w, sc, _root = library
    w._suppress_path(sc, 30)
    seen = []
    w.on_change = lambda status: seen.append(status)
    monkeypatch.setattr(watcher.GLib, "idle_add",
                        lambda fn, *a: fn(*a))

    w._do_sidecar_refresh(sc)

    assert seen == []


def test_the_refresh_does_not_write_to_the_index(library, monkeypatch):
    """Whoever replaced the file updated the row; touching it here
    would race them."""
    w, sc, _root = library
    monkeypatch.setattr(watcher.GLib, "idle_add", lambda fn, *a: None)
    monkeypatch.setattr(
        index, "upsert",
        lambda *a, **k: pytest.fail("refresh must not upsert"))

    w._do_sidecar_refresh(sc)


def test_a_renamed_sidecar_reaches_the_refresh(library, monkeypatch):
    """The event the atomic write actually produces."""
    w, sc, _root = library
    called = []
    monkeypatch.setattr(w, "_spawn",
                        lambda fn, *a: called.append((fn.__name__, a)))
    from gi.repository import Gio

    watcher.LibraryWatcher._on_changed(
        w, None, None, _FakeFile(sc), Gio.FileMonitorEvent.RENAMED)

    assert called and called[0][0] == "_do_sidecar_refresh"


class _FakeFile:
    def __init__(self, path):
        self._path = path

    def get_path(self):
        return self._path
