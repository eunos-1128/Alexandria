"""Thumbnails on feed cards, fetched off the main thread.

Retraction Watch posts carry a lead image and a wall of text cards is
much easier to scan with one — but a card is built during a UI
refresh, so the fetching cannot happen inline. Hence a small work
queue: `request(url, on_ready)`, callback on the main thread, never a
wait.

Measured against the live feed on 2026-09-26: every one of the ten
items had a lead image, always as the first `<img>` inside
`content:encoded` — there is no `<media:content>`, `<media:thumbnail>`
or `<enclosure>` to ask instead. WordPress serves a ladder of resized
copies, and asking for the 300 px rung where the URL admits one is
15 KB instead of 190 KB; where it does not, the original can be
384 KB, which is what the byte cap is for.
"""

import os
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import feed_images, index, retractionwatch

FIXTURE = os.path.join(HERE, "data", "retractionwatch-feed.xml")


@pytest.fixture
def posts():
    with open(FIXTURE, encoding="utf-8") as fh:
        return retractionwatch.parse_feed(fh.read())


# ---- finding the image -----------------------------------------------

def test_the_lead_image_is_picked_up(posts):
    assert posts[0]["image_url"].endswith(
        "/wp-content/uploads/2026/09/iStock-2207141986-2-300x200.jpg")


def test_a_smaller_copy_is_asked_for(posts):
    """The feed links the 1024 px copy; a 132 px card does not need
    190 KB of it."""
    assert "-1024x" not in posts[0]["image_url"]
    assert "-300x200" in posts[0]["image_url"]


@pytest.mark.parametrize("given,wanted", [
    # Aspect ratio kept, as WordPress itself would.
    ("https://x/a-1024x683.jpg", "https://x/a-300x200.jpg"),
    ("https://x/a-1024x768.jpeg", "https://x/a-300x225.jpeg"),
    # Already small enough, or no size in the name: left alone, since
    # a guess that misses costs a 404.
    ("https://x/a-300x200.jpg", "https://x/a-300x200.jpg"),
    ("https://x/a-150x150.png", "https://x/a-150x150.png"),
    ("https://x/stoten.jpg", "https://x/stoten.jpg"),
    ("https://x/rw_16x9.jpg", "https://x/rw_16x9.jpg"),
])
def test_the_size_downshift(given, wanted):
    assert retractionwatch._smaller_wordpress_copy(given) == wanted


def test_a_post_with_no_image_says_none():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>T</title><link>https://x/1</link>
            <description><![CDATA[Just words.]]></description></item>
    </channel></rss>"""

    assert retractionwatch.parse_feed(xml)[0]["image_url"] is None


def test_a_data_uri_is_not_followed():
    """Only http(s): a tracking pixel or an inline blob is not a
    thumbnail, and neither is something we should fetch."""
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>T</title><link>https://x/1</link>
        <description><![CDATA[<img src="data:image/gif;base64,R0lGOD" />]]>
        </description></item>
    </channel></rss>"""

    assert retractionwatch.parse_feed(xml)[0]["image_url"] is None


# ---- the cache -------------------------------------------------------

def test_the_path_is_derived_from_the_url(tmp_path):
    a = feed_images.cached_path("https://x/one.jpg", str(tmp_path))
    b = feed_images.cached_path("https://x/one.jpg", str(tmp_path))
    c = feed_images.cached_path("https://x/two.jpg", str(tmp_path))

    assert a == b and a != c
    assert a.endswith(".jpg")


def test_two_posts_may_both_have_cover_jpg(tmp_path):
    """Named by a hash, not a basename — a remote filename is not
    ours to trust, and collisions are otherwise certain."""
    a = feed_images.cached_path("https://one.example/cover.jpg",
                                str(tmp_path))
    b = feed_images.cached_path("https://two.example/cover.jpg",
                                str(tmp_path))

    assert a != b


def test_an_unexpected_extension_is_not_used_as_one(tmp_path):
    path = feed_images.cached_path("https://x/thing.php?id=2", str(tmp_path))

    assert path.endswith(".img")


# ---- downloading -----------------------------------------------------

class _Resp:
    def __init__(self, data, ctype="image/jpeg", length=None):
        self._data = data
        self.headers = {"Content-Type": ctype}
        if length is not None:
            self.headers["Content-Length"] = str(length)

    def read(self, n=None):
        return self._data[:n] if n else self._data

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False


def _serve(monkeypatch, resp):
    monkeypatch.setattr(feed_images.urllib.request, "urlopen",
                        lambda *a, **k: resp if not isinstance(resp, Exception)
                        else (_ for _ in ()).throw(resp))


def test_an_image_is_written_to_the_cache(tmp_path, monkeypatch):
    _serve(monkeypatch, _Resp(b"\xff\xd8\xff-jpeg-bytes"))

    path = feed_images.download("https://x/a.jpg", str(tmp_path))

    assert path and os.path.isfile(path)
    assert open(path, "rb").read() == b"\xff\xd8\xff-jpeg-bytes"


def test_html_is_not_an_image(tmp_path, monkeypatch):
    """A 200 carrying an error page is the common failure, and
    writing it would cache a broken thumbnail for ever."""
    _serve(monkeypatch, _Resp(b"<html>404</html>", ctype="text/html"))

    assert feed_images.download("https://x/a.jpg", str(tmp_path)) is None
    assert os.listdir(tmp_path) == []


def test_something_enormous_is_abandoned(tmp_path, monkeypatch):
    _serve(monkeypatch, _Resp(b"x" * 500, length=99 * 1024 * 1024))

    assert feed_images.download("https://x/a.jpg", str(tmp_path)) is None


def test_an_undeclared_enormous_body_is_also_abandoned(tmp_path,
                                                       monkeypatch):
    """No Content-Length, so the cap has to hold on what arrives."""
    _serve(monkeypatch, _Resp(b"x" * 5000))

    assert feed_images.download("https://x/a.jpg", str(tmp_path),
                                max_bytes=1000) is None


def test_nothing_half_written_is_left_behind(tmp_path, monkeypatch):
    _serve(monkeypatch, OSError("connection reset"))

    assert feed_images.download("https://x/a.jpg", str(tmp_path)) is None
    assert os.listdir(tmp_path) == [], "not even a .part file"


def test_a_cached_file_is_not_fetched_again(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        feed_images.urllib.request, "urlopen",
        lambda *a, **k: calls.append(1) or _Resp(b"jpeg"))
    feed_images.download("https://x/a.jpg", str(tmp_path))

    feed_images.download("https://x/a.jpg", str(tmp_path))

    assert len(calls) == 1


# ---- the queue -------------------------------------------------------

def _queue(tmp_path, downloader):
    """A queue that runs callbacks inline instead of on a GTK loop."""
    return feed_images.ImageQueue(
        workers=2, directory=str(tmp_path),
        dispatch=lambda fn, *a: fn(*a), downloader=downloader)


def _wait_for(predicate, seconds=5.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_a_request_calls_back_with_the_path(tmp_path):
    got = []
    q = _queue(tmp_path, lambda url, d: os.path.join(d, "fake.jpg"))
    try:
        q.request("https://x/a.jpg", got.append)

        assert _wait_for(lambda: got)
        assert got == [os.path.join(str(tmp_path), "fake.jpg")]
    finally:
        q.stop()


def test_the_same_url_is_fetched_once_for_every_card(tmp_path):
    """Two cards, one picture: the second waits on the first fetch
    rather than starting its own."""
    downloads = []
    started = threading.Event()
    release = threading.Event()

    def slow(url, d):
        downloads.append(url)
        started.set()
        release.wait(5)
        return os.path.join(d, "fake.jpg")

    got = []
    q = _queue(tmp_path, slow)
    try:
        q.request("https://x/a.jpg", got.append)
        assert started.wait(5), "first request is in flight"
        q.request("https://x/a.jpg", got.append)
        release.set()

        assert _wait_for(lambda: len(got) == 2)
        assert downloads == ["https://x/a.jpg"], "downloaded once"
    finally:
        release.set()
        q.stop()


def test_a_failed_download_calls_nobody_back(tmp_path):
    """The card keeps the space it reserved and stays pictureless —
    better than a callback with None that every caller must check."""
    got = []
    q = _queue(tmp_path, lambda url, d: None)
    try:
        q.request("https://x/a.jpg", got.append)
        time.sleep(0.2)

        assert got == []
    finally:
        q.stop()


def test_a_downloader_that_raises_does_not_kill_the_worker(tmp_path):
    got = []

    def boom(url, d):
        if "bad" in url:
            raise RuntimeError("kaboom")
        return os.path.join(d, "ok.jpg")

    q = _queue(tmp_path, boom)
    try:
        q.request("https://x/bad.jpg", got.append)
        q.request("https://x/good.jpg", got.append)

        assert _wait_for(lambda: got), "the queue survived the exception"
    finally:
        q.stop()


def test_an_already_cached_url_still_goes_through_the_dispatcher(tmp_path):
    """A callback that sometimes runs during widget construction and
    sometimes later is the harder thing to reason about."""
    path = feed_images.cached_path("https://x/a.jpg", str(tmp_path))
    open(path, "wb").write(b"jpeg")
    dispatched = []
    q = feed_images.ImageQueue(
        workers=1, directory=str(tmp_path),
        dispatch=lambda fn, *a: dispatched.append((fn, a)),
        downloader=lambda *a: pytest.fail("should not download"))
    try:
        q.request("https://x/a.jpg", lambda p: None)

        assert len(dispatched) == 1
        assert dispatched[0][1] == (path,)
    finally:
        q.stop()


def test_nothing_is_queued_after_stopping(tmp_path):
    q = _queue(tmp_path, lambda url, d: pytest.fail("stopped"))
    q.stop()

    q.request("https://x/a.jpg", lambda p: None)
    time.sleep(0.1)


# ---- pruning ---------------------------------------------------------

def test_old_thumbnails_are_swept_up(tmp_path):
    old = tmp_path / "old.jpg"
    new = tmp_path / "new.jpg"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    ancient = time.time() - 100 * 86400
    os.utime(old, (ancient, ancient))

    removed = feed_images.prune(max_age_days=60, directory=str(tmp_path))

    assert removed == 1
    assert not old.exists() and new.exists()


def test_pruning_a_missing_directory_is_not_an_error(tmp_path):
    assert feed_images.prune(directory=str(tmp_path / "nope")) == 0


# ---- the column ------------------------------------------------------

def test_the_image_url_round_trips(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")

    index.upsert_discovered(conn, sid, {
        "title": "A post", "source_url": "https://x/?p=1", "doi": None,
        "image_url": "https://x/lead-300x200.jpg"})

    row = index.discovered_for(conn, sid)[0]
    assert row["image_url"] == "https://x/lead-300x200.jpg"


def test_a_row_without_an_image_is_fine(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "openalex_query", "T", "q")

    index.upsert_discovered(conn, sid, {"doi": "10.1/x", "title": "P"})

    assert index.discovered_for(conn, sid)[0]["image_url"] is None
