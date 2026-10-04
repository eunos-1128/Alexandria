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


# ---- the log file ----------------------------------------------------
#
# Nearly every fix this autumn began with a terminal: the sidebar
# off-by-one, the import that hung, the summary showing raw Markdown.
# Launched from a desktop icon — or from Flathub, where the app now
# lives — stdout goes nowhere the user will ever look, so when a
# stranger says "it didn't work" there is nothing to ask them for.
#
# The file is written *alongside* the terminal rather than instead of
# it: stdout and stderr are teed, so all 58 existing `print("[…]")`
# sites land in it without being rewritten. Lines that do not already
# carry one get a timestamp, which is why the tee buffers to the
# newline rather than passing writes straight through — `print` emits
# the text and the "\n" as separate writes.

import os

LOG_NAME = "alexandria.log"
# Two files of this, so the log cannot grow without bound on a machine
# nobody is watching. Big enough to hold a long session: the watcher
# alone writes a dozen lines per import.
MAX_BYTES = 2 * 1024 * 1024

_tee_installed = False


def state_dir():
    base = (os.environ.get("XDG_STATE_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "state"))
    return os.path.join(base, "Alexandria")


def log_path():
    """Where the log file lives. Under Flatpak this is inside the
    app's own directory, which is a real path on the host — so a
    reader outside the sandbox can still open it."""
    return os.path.join(state_dir(), LOG_NAME)


def rotate(path=None, max_bytes=MAX_BYTES):
    """Keep one previous log. Returns True if a rotation happened.

    Called when the file is opened and again when it outgrows
    `max_bytes` during a session — an all-day session with the
    refreshers running would otherwise be unbounded."""
    path = path or log_path()
    try:
        if os.path.getsize(path) < max_bytes:
            return False
    except OSError:
        return False
    try:
        os.replace(path, path + ".1")
        return True
    except OSError:
        return False


class _LogFile:
    """The open log file, shared by both tees.

    One object rather than a handle each: when the file outgrows its
    limit mid-session it is rotated and reopened, and a tee holding
    its own handle would go on writing to a closed file — silently,
    since a log is never worth an exception. stderr losing its lines
    at the moment the log fills up is precisely the wrong failure."""

    def __init__(self, path, handle, max_bytes, banner=()):
        self.path = path
        self._handle = handle
        # Floored: the banner is re-emitted on every rotation, so a
        # limit smaller than the banner would rotate on each line and
        # grind the log down to a header. 4 KiB is well under any real
        # setting and still lets a test exercise rotation.
        self._max = max(max_bytes, 4096)
        self._banner = list(banner)
        self._lock = threading.Lock()

    def write_line(self, line):
        with self._lock:
            # Our own lines already open with "[HH:MM:SS.mmm "; a bare
            # print does not, and undated lines are what made the
            # watcher's output impossible to line up against anything.
            if not _looks_stamped(line):
                line = "[{}] {}".format(timestamp(), line)
            self._handle.write(line + "\n")
            self._handle.flush()
            if self._handle.tell() > self._max:
                self._rotate()

    def _rotate(self):
        self._handle.close()
        rotate(self.path, max_bytes=0)
        self._handle = open(self.path, "a", encoding="utf-8")
        # Written straight to the handle, not through `log()`: we are
        # inside `write_line` with the lock held, and the lock is not
        # reentrant. Without this a rotated log has no header, which
        # is the half of a bug report that says which build it is.
        for line in self._banner:
            self._handle.write("[{}] {}\n".format(timestamp(), line))
        self._handle.flush()


class _Tee:
    """Writes to the real stream and to the log file.

    Buffers to the newline because `print` emits the text and the
    "\n" as separate writes, and a timestamp per write would chop
    every line in two."""

    def __init__(self, stream, logfile):
        self._stream = stream
        self._log = logfile
        # Per thread, not one shared buffer. `print(x)` writes the
        # text and the "\n" as two calls, so with a single buffer a
        # second thread printing in between concatenates into the
        # first thread's unfinished line: measured as
        # "[thread0] line 0[thread1] line 0". A lock around each write
        # does not help -- the gap is *between* two locked writes.
        self._local = threading.local()

    def write(self, text):
        self._stream.write(text)
        try:
            buf = getattr(self._local, "buf", "") + text
            lines = buf.split("\n")
            self._local.buf = lines.pop()
            for line in lines:
                self._log.write_line(line)
        except Exception:
            pass          # a log file is never worth an exception
        return len(text)

    def flush(self):
        self._stream.flush()

    def isatty(self):
        return getattr(self._stream, "isatty", lambda: False)()

    def fileno(self):
        return self._stream.fileno()


def _looks_stamped(line):
    """Does this line already begin `[HH:MM:SS.mmm `?"""
    return (len(line) > 14 and line[0] == "[" and line[3] == ":"
            and line[6] == ":" and line[9] == ".")


def environment():
    """What build this is, as `key=value` strings for the head of the
    log.

    A copied log is what a bug report carries, and until this it said
    nothing about which Alexandria wrote it. Poppler is in the list
    because the Flatpak bundles its own: on 2026-10-03 the published
    build was carrying 24.11.0 while this machine ran 26.08.0, which
    is exactly the kind of difference that makes a rendering bug
    impossible to reproduce.

    Every lookup is guarded: this runs before the GUI exists and must
    not be the thing that stops the application starting."""
    import platform

    out = []
    try:
        from . import __version__
        out.append("alexandria={}".format(__version__))
    except Exception:
        pass
    try:
        from . import sandbox
        if sandbox.in_flatpak():
            out.append("flatpak={}".format(sandbox.app_id() or "yes"))
        else:
            out.append("flatpak=no")
    except Exception:
        pass
    out.append("python={}".format(platform.python_version()))
    try:
        from gi.repository import Gtk
        out.append("gtk={}.{}.{}".format(Gtk.get_major_version(),
                                         Gtk.get_minor_version(),
                                         Gtk.get_micro_version()))
    except Exception:
        pass
    try:
        from gi.repository import Adw
        out.append("libadwaita={}.{}.{}".format(
            Adw.MAJOR_VERSION, Adw.MINOR_VERSION, Adw.MICRO_VERSION))
    except Exception:
        pass
    try:
        import gi
        gi.require_version("Poppler", "0.18")
        from gi.repository import Poppler
        out.append("poppler={}".format(Poppler.get_version()))
    except Exception:
        pass
    return out


def start_file_logging(path=None, max_bytes=MAX_BYTES):
    """Tee stdout and stderr into the log file. Returns its path, or
    None when it could not be opened — a read-only home should cost
    the log, not the application."""
    global _tee_installed
    if _tee_installed:
        return log_path()
    path = path or log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        rotate(path, max_bytes)
        handle = open(path, "a", encoding="utf-8")
    except OSError:
        return None
    banner = ["logging to {}".format(path)] + environment()
    logfile = _LogFile(path, handle, max_bytes, banner=banner)
    sys.stdout = _Tee(sys.stdout, logfile)
    sys.stderr = _Tee(sys.stderr, logfile)
    _tee_installed = True
    for line in banner:
        log("log", line)
    return path


def read_tail(path=None, max_bytes=200 * 1024):
    """The last `max_bytes` of the log, for showing in a window.
    Starts at a line boundary so the first line is not a fragment."""
    path = path or log_path()
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()
            return fh.read()
    except OSError:
        return ""
