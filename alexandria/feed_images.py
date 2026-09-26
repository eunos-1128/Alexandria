"""Thumbnails for feed cards, fetched off the main thread.

Retraction Watch posts carry a lead image, and a wall of text cards
is much easier to scan with one. But a card is built during a UI
refresh, and a refresh that stops to download ten JPEGs is a frozen
window — so nothing here is allowed to happen inline.

The shape is a small work queue:

    fetcher().request(url, on_ready)

`on_ready(path)` runs on the GTK main thread, once, if and when the
file is on disk. Already cached, and it fires immediately on the
next idle — the caller draws a placeholder and never waits either
way. The card holds no thread and no lock; if the row is gone by the
time the image arrives, the callback checks and does nothing.

Three things it is careful about, all learned from the live feed:

  * **Size.** WordPress serves a ladder of resized copies, and
    `retractionwatch.parse_feed` already asks for the 300 px rung
    where the URL admits one (15 KB instead of 190 KB). Where it
    does not, the original can be 384 KB, so there is a hard byte
    cap and anything over it is abandoned rather than truncated
    into a broken file.
  * **What came back.** A 200 carrying HTML is an error page, not a
    picture, so the content type is checked before anything is
    written.
  * **Writing.** Downloads land on a temporary name and are renamed
    into place, so a half-written file is never served from the
    cache — including after a crash or a pulled cable.

The cache is disposable: delete the directory and the feed refills
it. It is pruned on the same 60-day horizon as `discovered`, so it
cannot outgrow the rows that refer to it.
"""

import hashlib
import os
import queue
import threading
import time
import urllib.request

# Enough for a 300 px thumbnail many times over; small enough that a
# mis-parsed URL pointing at a video cannot fill the disk.
MAX_BYTES = 4 * 1024 * 1024
TIMEOUT = 15
# Two: the feed shows ten cards, and a browser would not open ten
# connections to one small site either.
WORKERS = 2
# Matches index.DISCOVERED_RETENTION_DAYS — a cached image whose row
# has been pruned is dead weight.
MAX_AGE_DAYS = 60

_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif"}


def _xdg_data_home():
    return (os.environ.get("XDG_DATA_HOME")
            or os.path.join(os.path.expanduser("~"), ".local", "share"))


def cache_dir():
    path = os.path.join(_xdg_data_home(), "Alexandria", "feed-images")
    os.makedirs(path, exist_ok=True)
    return path


def cached_path(url, directory=None):
    """Where `url` is or would be cached.

    Named by a hash of the URL, not by its basename: two posts can
    both have `cover.jpg`, and a remote name is not ours to trust as
    a filename."""
    digest = hashlib.sha256((url or "").encode("utf-8")).hexdigest()[:32]
    ext = os.path.splitext((url or "").split("?")[0])[1].lower()
    if ext not in _EXTENSIONS:
        ext = ".img"
    return os.path.join(directory or cache_dir(), digest + ext)


def is_cached(url, directory=None):
    if not url:
        return False
    return os.path.exists(cached_path(url, directory))


def download(url, directory=None, timeout=TIMEOUT, max_bytes=MAX_BYTES):
    """Fetch `url` into the cache and return the path, or None.

    Never raises: a thumbnail that cannot be had is a card without a
    picture, which is the state it was in a moment ago anyway."""
    if not url:
        return None
    target = cached_path(url, directory)
    if os.path.exists(target):
        return target
    req = urllib.request.Request(
        url, headers={"User-Agent": "alexandria",
                      "Accept": "image/*"})
    tmp = target + ".part"
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            ctype = (resp.headers.get("Content-Type") or "").lower()
            if not ctype.startswith("image/"):
                return None
            declared = resp.headers.get("Content-Length")
            if declared and int(declared) > max_bytes:
                return None
            data = resp.read(max_bytes + 1)
        if not data or len(data) > max_bytes:
            return None
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "wb") as fh:
            fh.write(data)
        os.replace(tmp, target)
        return target
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


def prune(max_age_days=MAX_AGE_DAYS, directory=None):
    """Drop cached images older than the rows that point at them.
    Returns the number removed."""
    directory = directory or cache_dir()
    cutoff = time.time() - max_age_days * 86400
    removed = 0
    try:
        names = os.listdir(directory)
    except OSError:
        return 0
    for name in names:
        path = os.path.join(directory, name)
        try:
            if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
                os.unlink(path)
                removed += 1
        except OSError:
            continue
    return removed


def _idle_dispatch(fn, *args):
    """Hand a callback to the GTK main loop. Importable without GTK
    so the queue can be tested headless."""
    try:
        from gi.repository import GLib
    except Exception:
        fn(*args)
        return
    GLib.idle_add(lambda: (fn(*args), False)[1])


class ImageQueue:
    """A few worker threads and a work list, with the two properties
    a UI needs: the same URL is never fetched twice concurrently, and
    every callback lands on the main thread."""

    def __init__(self, workers=WORKERS, directory=None,
                 dispatch=_idle_dispatch, downloader=download):
        self._q = queue.Queue()
        self._directory = directory
        self._dispatch = dispatch
        self._download = downloader
        self._lock = threading.Lock()
        # url -> [callbacks]. Presence means "in flight", so a second
        # card wanting the same picture waits on the first fetch
        # rather than starting its own.
        self._waiting = {}
        self._stopping = threading.Event()
        self._threads = [
            threading.Thread(target=self._work, daemon=True,
                             name="feed-image-%d" % i)
            for i in range(max(1, workers))]
        for t in self._threads:
            t.start()

    def request(self, url, on_ready):
        """Ask for `url`; `on_ready(path)` runs on the main thread.

        A cached file still goes through the dispatcher rather than
        calling back inline: a callback that sometimes runs during
        widget construction and sometimes later is the harder thing
        to reason about."""
        if not url or self._stopping.is_set():
            return
        path = cached_path(url, self._directory)
        if os.path.exists(path):
            self._dispatch(on_ready, path)
            return
        with self._lock:
            if url in self._waiting:
                self._waiting[url].append(on_ready)
                return
            self._waiting[url] = [on_ready]
        self._q.put(url)

    def _work(self):
        while not self._stopping.is_set():
            try:
                url = self._q.get(timeout=0.5)
            except queue.Empty:
                continue
            if url is None:
                break
            try:
                path = self._download(url, self._directory)
            except Exception:
                path = None
            with self._lock:
                callbacks = self._waiting.pop(url, [])
            if path:
                for cb in callbacks:
                    self._dispatch(cb, path)
            self._q.task_done()

    def stop(self):
        """Let the workers finish the one in hand and exit.

        Not wired to the feed window closing: the queue is
        process-wide and a second window may still be drawing from
        it. The workers are daemons, so they end with the process
        anyway; this exists for tests and for a future shutdown
        hook."""
        self._stopping.set()
        for _ in self._threads:
            self._q.put(None)


_fetcher = None
_fetcher_lock = threading.Lock()


def fetcher():
    """The process-wide queue, made on first use. One queue rather
    than one per window, so two feed windows share both the workers
    and the in-flight dedupe."""
    global _fetcher
    with _fetcher_lock:
        if _fetcher is None or _fetcher._stopping.is_set():
            _fetcher = ImageQueue()
        return _fetcher
