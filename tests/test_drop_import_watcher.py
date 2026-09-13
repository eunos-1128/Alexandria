"""A drop must not race the watcher for the same files.

Reported 2026-09-09: dropping seven PDFs onto the window froze
Alexandria for about thirty seconds. The drop copies each file into
the library root, which the watcher sees as an externally-added PDF
and imports itself — in parallel with the import the drop worker is
already running. The two contend for the SQLite writer and the GUI
thread waits behind them, for up to `index._BUSY_TIMEOUT_MS`, which
is exactly the thirty seconds observed.

The menu-driven import has always suppressed watcher events on the
paths it writes. The drop path never did.
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
from alexandria import index


class FakeWatcher:
    def __init__(self):
        self.calls = []

    def suppress(self, path, secs):
        self.calls.append((os.path.abspath(path), secs))


def _stub_window(tmp_path, watcher):
    win = types.SimpleNamespace(
        library_root=str(tmp_path / "library"),
        library_watcher=watcher,
        _ghost_for_doi=lambda doi: None,
        _on_drop_done=lambda results: None,
        _on_import_start=lambda basename: None,
    )
    win._suppress_watcher = types.MethodType(
        browse.BrowserWindow._suppress_watcher, win)
    return win


def test_the_target_is_suppressed_before_it_is_copied(tmp_path,
                                                      monkeypatch):
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    watcher = FakeWatcher()
    win = _stub_window(tmp_path, watcher)
    conn = index.open_db(str(tmp_path / "lib.db"))

    order = []
    real_copy = browse.shutil.copy2

    def spy_copy(a, b, *args, **kw):
        order.append("copy")
        return real_copy(a, b, *args, **kw)

    monkeypatch.setattr(browse.shutil, "copy2", spy_copy)
    monkeypatch.setattr(browse.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: None)
    monkeypatch.setattr(browse.importer, "import_pdf",
                        lambda c, p, **_kw: ({"title": "T"}, "imported"))
    monkeypatch.setattr(browse.GLib, "idle_add", lambda *a, **k: None)

    def spy_suppress(path, secs=120):
        order.append("suppress")
        watcher.calls.append((os.path.abspath(path), secs))

    monkeypatch.setattr(win, "_suppress_watcher", spy_suppress)

    browse.BrowserWindow._do_drop_import_with_conn(win, conn, [str(src)])

    assert order == ["suppress", "copy"], \
        "the watcher can see the file the moment it appears"
    target = os.path.join(win.library_root, "paper.pdf")
    assert watcher.calls[0][0] == os.path.abspath(target)


def test_every_dropped_file_is_suppressed(tmp_path, monkeypatch):
    """Seven at once was the reported case."""
    names = ["a.pdf", "b.pdf", "c.pdf"]
    for n in names:
        (tmp_path / n).write_bytes(b"%PDF-1.4 fake")
    watcher = FakeWatcher()
    win = _stub_window(tmp_path, watcher)
    conn = index.open_db(str(tmp_path / "lib.db"))

    monkeypatch.setattr(browse.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: None)
    monkeypatch.setattr(browse.importer, "import_pdf",
                        lambda c, p, **_kw: ({"title": "T"}, "imported"))
    monkeypatch.setattr(browse.GLib, "idle_add", lambda *a, **k: None)

    browse.BrowserWindow._do_drop_import_with_conn(
        win, conn, [str(tmp_path / n) for n in names])

    suppressed = {os.path.basename(p) for p, _s in watcher.calls}
    assert suppressed == set(names)


def test_a_missing_watcher_is_not_an_error(tmp_path):
    """Windows are built before the watcher starts, and some callers
    have none at all."""
    win = _stub_window(tmp_path, None)
    win._suppress_watcher("/x/y.pdf")      # must not raise


def test_the_suppression_outlasts_a_slow_import(tmp_path):
    """The import that follows makes network calls; the window has to
    cover it or the watcher fires late and imports anyway."""
    watcher = FakeWatcher()
    win = _stub_window(tmp_path, watcher)

    win._suppress_watcher("/x/y.pdf")

    assert watcher.calls[0][1] >= 60


def test_a_drop_raises_the_importing_toast(tmp_path, monkeypatch):
    """Reported 2026-09-13: dropping a PDF onto the window worked but
    said nothing.

    The "Importing x…" toast is raised by the watcher, and this path
    suppresses the watcher on purpose — so the route a user is most
    likely to take was the one route with no toast at all."""
    names = ["one.pdf", "two.pdf"]
    for n in names:
        (tmp_path / n).write_bytes(b"%PDF-1.4 fake " + n.encode())
    win = _stub_window(tmp_path, FakeWatcher())
    conn = index.open_db(str(tmp_path / "lib.db"))

    started = []
    win._on_import_start = lambda basename: started.append(basename)
    monkeypatch.setattr(browse.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: None)
    monkeypatch.setattr(browse.importer, "import_pdf",
                        lambda c, p, **_kw: ({"title": "T"}, "imported"))
    # idle_add hops to the main loop; here, call straight through so the
    # test sees what the main loop would have.
    monkeypatch.setattr(browse.GLib, "idle_add",
                        lambda fn, *args: fn(*args))

    browse.BrowserWindow._do_drop_import_with_conn(
        win, conn, [str(tmp_path / n) for n in names])

    assert started == names


def test_the_toast_fires_before_the_slow_part(tmp_path, monkeypatch):
    """A toast raised after the import would be describing the past:
    the import is the multi-second bit it is meant to cover."""
    src = tmp_path / "paper.pdf"
    src.write_bytes(b"%PDF-1.4 fake")
    win = _stub_window(tmp_path, FakeWatcher())
    conn = index.open_db(str(tmp_path / "lib.db"))

    order = []
    win._on_import_start = lambda basename: order.append("toast")
    monkeypatch.setattr(browse.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: order.append("scan"))
    monkeypatch.setattr(
        browse.importer, "import_pdf",
        lambda c, p, **_kw: (order.append("import"),
                             ({"title": "T"}, "imported"))[1])
    monkeypatch.setattr(browse.GLib, "idle_add",
                        lambda fn, *args: fn(*args))

    browse.BrowserWindow._do_drop_import_with_conn(win, conn, [str(src)])

    assert order[0] == "toast"
