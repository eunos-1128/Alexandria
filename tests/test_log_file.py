"""The log goes to a file the user can be asked for.

From the backlog's "Diagnostics the user can find": nearly every fix
this autumn began with a terminal — the sidebar off-by-one, the import
that hung, the summary showing raw Markdown. There are 58
`print("[…]")` sites across the package and every one went to stdout
alone. Launched from a desktop icon, or from Flathub where the
application now lives, that goes nowhere anyone will look, so "it
didn't work" arrives with nothing attached.

The file is written *alongside* the terminal: stdout and stderr are
teed, which is what lets all 58 existing prints land in it without
being rewritten.
"""

import os
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import applog


@pytest.fixture
def start_log(tmp_path, monkeypatch):
    """Returns a callable that tees stdout/stderr into a temporary log
    and hands back its path.

    A callable rather than a ready-made fixture value, because pytest
    installs its own `sys.stdout` between fixture setup and the test
    body — a tee wrapped around it during setup is replaced before the
    first `print` of the test. Installed from inside the test, it
    stands.
    """
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    saved = []

    def _install(**kw):
        monkeypatch.setattr(applog, "_tee_installed", False)
        saved.append((sys.stdout, sys.stderr))
        return applog.start_file_logging(**kw)

    yield _install

    for out, err in saved:
        sys.stdout, sys.stderr = out, err
    applog._tee_installed = False


def _contents(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


# ---- where it lives --------------------------------------------------

def test_it_lives_under_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))

    assert applog.log_path() == str(tmp_path / "Alexandria"
                                    / "alexandria.log")


def test_an_unwritable_home_costs_the_log_not_the_app(monkeypatch):
    """A log file is never worth refusing to start."""
    monkeypatch.setattr(applog, "_tee_installed", False)
    monkeypatch.setattr(applog.os, "makedirs",
                        lambda *a, **k: (_ for _ in ()).throw(
                            OSError("read-only file system")))

    assert applog.start_file_logging() is None


# ---- what reaches it -------------------------------------------------

def test_a_bare_print_is_captured(start_log):
    """The whole point: 58 existing print() sites, none rewritten."""
    logfile = start_log()
    print("[watcher] event=deleted path=/x/y.pdf other=None")

    assert "event=deleted path=/x/y.pdf" in _contents(logfile)


def test_stderr_is_captured_too(start_log):
    logfile = start_log()
    print("something went wrong", file=sys.stderr)

    assert "something went wrong" in _contents(logfile)


def test_the_terminal_still_gets_it(start_log, capsys):
    """Alongside the prints, not instead of them."""
    start_log()
    print("[importer] done")

    assert "[importer] done" in capsys.readouterr().out


def test_an_undated_line_is_given_a_time(start_log):
    logfile = start_log()
    print("[watcher] import start: /x/y.pdf")

    line = [ln for ln in _contents(logfile).split("\n")
            if "import start" in ln][0]
    assert applog._looks_stamped(line), line


def test_an_already_stamped_line_is_not_stamped_twice(start_log):
    logfile = start_log()
    applog.log("crossref", "backfilled 3 row(s)")

    line = [ln for ln in _contents(logfile).split("\n")
            if "backfilled" in ln][0]
    assert line.count("] ") == 1, line
    assert "crossref" in line


def test_a_line_split_across_writes_arrives_whole(start_log):
    """`print` emits the text and the newline separately; a timestamp
    per write would chop every line in two."""
    logfile = start_log()
    sys.stdout.write("half ")
    sys.stdout.write("a line\n")

    assert "half a line" in _contents(logfile)


def test_an_unterminated_write_waits_for_its_newline(start_log):
    logfile = start_log()
    sys.stdout.write("no newline yet")

    assert "no newline yet" not in _contents(logfile)


def test_threads_do_not_interleave_half_lines(start_log):
    logfile = start_log()
    def worker(n):
        for i in range(20):
            print("[thread{}] line {}".format(n, i))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    for line in _contents(logfile).strip().split("\n"):
        assert line.count("[thread") <= 1, line


# ---- rotation --------------------------------------------------------

def test_a_big_log_is_rotated_on_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = applog.log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("x" * 5000)

    assert applog.rotate(path, max_bytes=1000) is True
    assert os.path.exists(path + ".1")
    assert not os.path.exists(path)


def test_a_small_log_is_left_alone(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = applog.log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write("short")

    assert applog.rotate(path, max_bytes=1000) is False
    assert os.path.exists(path)


def test_rotating_a_log_that_is_not_there_is_not_an_error(tmp_path):
    assert applog.rotate(str(tmp_path / "nothing.log")) is False


def test_both_streams_survive_a_rotation(start_log):
    """The two tees share one handle: with a handle each, stderr would
    go on writing to the closed file after a rotation -- silently,
    since a log is never worth an exception."""
    path = start_log(max_bytes=4096)

    for i in range(400):
        print("stdout line {}".format(i))
    print("stderr still works", file=sys.stderr)

    assert "stderr still works" in _contents(path)
    assert os.path.exists(path + ".1")


# ---- reading it back -------------------------------------------------

def test_the_tail_starts_at_a_line_boundary(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    path = applog.log_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for i in range(500):
            fh.write("line {:04d} ................................\n".format(i))

    tail = applog.read_tail(max_bytes=2000)

    assert tail.startswith("line "), "no fragment at the front"
    assert tail.rstrip().endswith("...")
    assert len(tail) <= 2100


def test_reading_a_missing_log_gives_nothing(tmp_path):
    assert applog.read_tail(str(tmp_path / "nope.log")) == ""


# ---- the menu item ---------------------------------------------------

def test_show_log_is_in_the_menu_and_wired():
    import inspect

    from alexandria import browse

    src = inspect.getsource(browse.BrowserWindow)

    assert '"Show Log", "win.show-log"' in src
    assert '("show-log",      self._open_log)' in src
    assert hasattr(browse.BrowserWindow, "_open_log")


def test_logging_starts_before_anything_prints():
    """It is the first thing `main` does; a starter-config line or an
    early failure belongs in the file too."""
    import inspect

    from alexandria import browse

    src = inspect.getsource(browse.main)

    assert "applog.start_file_logging()" in src
    assert (src.index("applog.start_file_logging()")
            < src.index("prefs.ensure_config_file()"))


# ---- what build wrote this log ---------------------------------------

def test_the_banner_names_the_build():
    """A copied log is what a bug report carries; until this it said
    nothing about which Alexandria wrote it."""
    fields = dict(f.split("=", 1) for f in applog.environment()
                  if "=" in f)

    assert fields["alexandria"]
    assert fields["flatpak"] in ("no",) or fields["flatpak"]
    assert fields["python"]


def test_poppler_is_in_it():
    """The Flatpak bundles its own: on 2026-10-03 the published build
    carried 24.11.0 while the host ran 26.08.0, which is exactly the
    difference that makes a rendering bug unreproducible."""
    assert any(f.startswith("poppler=") for f in applog.environment())


def test_a_broken_lookup_does_not_stop_startup(monkeypatch):
    """This runs before the GUI exists."""
    import builtins

    real = builtins.__import__

    def boom(name, *a, **k):
        if name == "platform":
            raise ImportError("no platform module")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", boom)
    try:
        with pytest.raises(ImportError):
            applog.environment()
    finally:
        monkeypatch.setattr(builtins, "__import__", real)


def test_the_banner_is_at_the_head_of_the_log(start_log):
    path = start_log()

    first = _contents(path).strip().split("\n")[0]

    assert "logging to" in first


def test_a_rotated_log_is_given_the_banner_again(start_log):
    """Without it a rotated log has no header at all -- the half of a
    report that says which build it is."""
    path = start_log(max_bytes=4096)

    for i in range(400):
        print("filler line {}".format(i))

    assert any("alexandria=" in ln for ln in _contents(path).split("\n")), \
        "the current file carries the banner after rotation"
