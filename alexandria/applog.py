"""One format for the application's terminal log.

Alexandria runs a lot of things at once — the watcher, the citation
refresher, the author-score walk, PDB indexing, the CrossRef backfill,
the feed refresher — and each of them used to `print()` in its own
shape. Interleaved in a terminal those lines are hard to attribute,
and without a clock on them they cannot be lined up against anything
the user did.

That mattered on 2026-10-03: a "Library updated (deleted)" message
turned out to be about a file deleted some time before the download
that appeared to have caused it, and the log could not say how long
before, because the watcher's lines carried no time.

    [12:50:15.431 watcher MainThread] import done: …/paper.pdf -> ok

The thread name is there for the same reason as the tag: several of
these walks run concurrently, and "which one is this" is otherwise
guesswork.
"""

import sys
import threading
import time


def timestamp():
    """`HH:MM:SS.mmm` — local time, millisecond resolution.

    Not ISO 8601 with a date: this is a terminal log read while the
    application is running, where the date is today and the width of
    each line matters more than being sortable across days.
    """
    return time.strftime("%H:%M:%S") + ".{:03d}".format(
        int((time.time() % 1) * 1000))


def log(tag, msg, stream=None):
    """Write one tagged, timestamped line.

    Unbuffered (`flush=True`): a crash or a kill should not take the
    last few lines with it, and those are usually the interesting
    ones."""
    print("[{} {} {}] {}".format(
        timestamp(), tag, threading.current_thread().name, msg),
        file=stream or sys.stdout, flush=True)
