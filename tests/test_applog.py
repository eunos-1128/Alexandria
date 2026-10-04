"""The terminal log carries a clock.

Reported 2026-10-03: a status bar reading "Library updated (deleted)"
appeared while adding a bioRxiv preprint to the library. The log held
the answer — the deletion was of a different paper entirely, some time
before the download — but could not prove it, because the watcher's
lines were bare `print()` with no time on them:

    [watcher] event=deleted path=…/gchojnowski_…_Jun2022.pdf other=None
    [watcher] event=created path=…/2026.02.21.706873.full.pdf.tmp

Nothing there says whether those are a second apart or an hour.

`browse._wlog` already timestamped the worker threads; the watcher,
which is the part most often read against something the user just
did, did not. Both now go through `applog`, so the lines interleave
in one shape.
"""

import os
import re
import sys
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import applog

# [12:50:15.431 watcher MainThread] message
LINE = re.compile(r"^\[(\d{2}):(\d{2}):(\d{2})\.(\d{3}) (\S+) (\S+)\] (.*)$")


def test_a_line_has_a_time_a_tag_and_a_thread(capsys):
    applog.log("watcher", "import done: /x/y.pdf -> ok")

    m = LINE.match(capsys.readouterr().out.rstrip("\n"))
    assert m, "the line does not match the documented shape"
    assert m.group(5) == "watcher"
    assert m.group(6) == threading.current_thread().name
    assert m.group(7) == "import done: /x/y.pdf -> ok"


def test_the_time_is_to_the_millisecond():
    """Two events a few milliseconds apart are the ones worth telling
    apart — a download and the import it triggers, say."""
    assert re.fullmatch(r"\d{2}:\d{2}:\d{2}\.\d{3}", applog.timestamp())


def test_the_thread_is_named_because_several_walk_at_once(capsys):
    done = threading.Event()

    def worker():
        applog.log("crossref", "fetching")
        done.set()

    t = threading.Thread(target=worker, name="Thread-7 (crossref)")
    t.start()
    t.join(5)

    assert done.is_set()
    assert "Thread-7 (crossref)" in capsys.readouterr().out


def test_a_message_with_braces_is_not_reformatted(capsys):
    """Paths and JSON fragments go through here; `log` must not treat
    the message as a format string."""
    applog.log("watcher", "sidecar {'doi': '10.64898/x'} rejected")

    assert "{'doi': '10.64898/x'}" in capsys.readouterr().out


# ---- the watcher uses it ---------------------------------------------

def test_the_watcher_has_no_bare_prints():
    """Every line it writes should be attributable and timed."""
    import inspect

    from alexandria import watcher

    src = inspect.getsource(watcher)

    assert "print(" not in src


def test_every_watcher_log_call_takes_one_message():
    """Two of these were `print(msg, exc)` — two positional arguments,
    which `_log` does not take and which would have raised at exactly
    the moment something had already gone wrong."""
    import ast
    import inspect

    from alexandria import watcher

    tree = ast.parse(inspect.getsource(watcher))
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", "") == "_log"]

    assert calls, "the watcher logs nothing at all?"
    assert all(len(c.args) == 1 for c in calls)


def test_the_watcher_tags_its_lines(capsys):
    from alexandria import watcher

    watcher._log("event=deleted path=/x/y.pdf other=None")

    m = LINE.match(capsys.readouterr().out.rstrip("\n"))
    assert m and m.group(5) == "watcher"


def test_browse_and_the_watcher_share_one_format(capsys):
    """Interleaved in a terminal, two shapes are harder to read than
    one."""
    from alexandria import browse, watcher

    browse._wlog("crossref", "backfilled 3 row(s)")
    watcher._log("import start: /x/y.pdf")

    lines = capsys.readouterr().out.rstrip("\n").split("\n")
    assert len(lines) == 2
    assert all(LINE.match(ln) for ln in lines)
