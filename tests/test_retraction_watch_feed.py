"""Following Retraction Watch as a subscription.

Asked for 2026-09-26. Two halves were on offer — the blog, and the
Crossref-hosted Retraction Watch *database* that could flag retracted
papers in the user's own library — and the blog is the one built; the
database is filed in BACKLOG.md.

Which matters here, because the blog cannot do the database's job:
across the whole 114 KB feed there are nine DOI-shaped strings, all in
prose. These rows are reporting *about* papers. They carry no DOI, are
never filed as papers, and the only thing to do with one is read it.

That is what forced the storage change. `discovered` is
UNIQUE(subscription_id, doi), and SQLite treats every NULL as
distinct, so without a second key the same ten posts would arrive
again on every refresh, for ever.

The fixture is the live feed of 2026-09-26, trimmed to two items with
their `content:encoded` bodies removed (the parser does not read
them).
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import feed, index, retractionwatch

FIXTURE = os.path.join(HERE, "data", "retractionwatch-feed.xml")


@pytest.fixture
def posts():
    with open(FIXTURE, encoding="utf-8") as fh:
        return retractionwatch.parse_feed(fh.read())


# ---- the parser ------------------------------------------------------

def test_the_feed_yields_its_posts(posts):
    assert len(posts) == 2
    assert posts[0]["title"] == (
        "Above and beyond: An editor wanted authors to know "
        "they’d been falsely cited")


def test_a_post_is_not_a_paper(posts):
    """No DOI, ever. The row must not look like something to import."""
    assert all(p["doi"] is None for p in posts)


def test_the_dedupe_key_is_the_guid(posts):
    """`<guid>` is stable; `<link>` changes if a post is re-slugged."""
    assert posts[0]["source_url"] == "https://retractionwatch.com/?p=136091"
    assert posts[0]["url"].startswith(
        "https://retractionwatch.com/2026/09/25/above-and-beyond")


def test_the_date_becomes_sortable(posts):
    """RFC 822 in the feed; the window sorts `published_date` as a
    string against CrossRef and OpenAlex rows, which are dates."""
    assert posts[0]["published_date"] == "2026-09-25"
    assert posts[1]["published_date"] == "2026-09-24"


def test_the_byline_is_kept(posts):
    assert posts[0]["authors"] == ["Alicia Gallegos"]


def test_the_post_tags_fill_the_journal_slot(posts):
    """A blog post has no journal, and the slot is right there."""
    assert posts[0]["journal"] == "reference problems"


def test_the_continue_reading_tail_is_removed(posts):
    """Every WordPress teaser ends with the same anchor. Left in, all
    ten cards end with the same nine words."""
    summary = posts[0]["abstract"]

    assert "Continue reading" not in summary
    assert "<a " not in summary and "</span>" not in summary
    assert summary.startswith("Like many college professors today")


def test_entities_are_resolved_not_shown(posts):
    for p in posts:
        assert "&#160;" not in (p["abstract"] or "")
        assert "&#8230;" not in (p["abstract"] or "")


def test_rubbish_in_is_empty_out():
    assert retractionwatch.parse_feed("not xml at all") == []
    assert retractionwatch.parse_feed("") == []


def test_an_item_with_no_title_is_skipped():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><link>https://x/1</link></item>
      <item><title>Real</title><link>https://x/2</link></item>
    </channel></rss>"""

    got = retractionwatch.parse_feed(xml)

    assert [p["title"] for p in got] == ["Real"]


def test_a_missing_date_is_not_fatal():
    xml = """<?xml version="1.0"?><rss version="2.0"><channel>
      <item><title>T</title><link>https://x/1</link>
            <pubDate>not a date</pubDate></item>
    </channel></rss>"""

    assert retractionwatch.parse_feed(xml)[0]["published_date"] is None


# ---- storing rows that have no DOI -----------------------------------

def _post(url, title="A post"):
    return {"title": title, "source_url": url, "url": url,
            "published_date": "2026-09-25", "doi": None,
            "authors": [], "abstract": None, "journal": None}


def test_a_doi_less_row_is_stored(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")

    assert index.upsert_discovered(conn, sid, _post("https://x/?p=1")) is True

    rows = index.discovered_for(conn, sid)
    assert len(rows) == 1
    assert rows[0]["doi"] is None
    assert rows[0]["source_url"] == "https://x/?p=1"


def test_the_same_post_does_not_arrive_twice(tmp_path):
    """The whole reason for the second key: the feed is a rolling
    window of ten, so every refresh re-offers what is already held."""
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")
    index.upsert_discovered(conn, sid, _post("https://x/?p=1"))

    assert index.upsert_discovered(conn, sid, _post("https://x/?p=1")) is False
    assert len(index.discovered_for(conn, sid)) == 1


def test_different_posts_both_land(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")

    index.upsert_discovered(conn, sid, _post("https://x/?p=1"))
    index.upsert_discovered(conn, sid, _post("https://x/?p=2"))

    assert len(index.discovered_for(conn, sid)) == 2


def test_two_subscriptions_are_fenced_from_each_other(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    a = index.add_subscription(conn, "retraction_watch", "RW", "")
    b = index.add_subscription(conn, "openalex_query", "T", "q")

    assert index.upsert_discovered(conn, a, _post("https://x/?p=1")) is True
    assert index.upsert_discovered(conn, b, _post("https://x/?p=1")) is True


def test_an_item_with_no_key_at_all_is_refused(tmp_path):
    """Nothing to recognise it by next time, so it would pile up."""
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")

    assert index.upsert_discovered(
        conn, sid, {"title": "no key", "doi": None}) is False


def test_doi_keyed_rows_still_dedupe_on_the_doi(tmp_path):
    """The new index must not disturb what the table was built for.
    Two rows, same DOI, different URLs: still one row."""
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "openalex_query", "T", "q")
    art = {"doi": "10.1107/S0907444910007493", "title": "Coot"}

    assert index.upsert_discovered(conn, sid, dict(art)) is True
    assert index.upsert_discovered(
        conn, sid, dict(art, source_url="https://elsewhere/x")) is False


def test_the_url_is_kept_so_the_row_can_be_read(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")
    index.upsert_discovered(conn, sid, _post("https://x/?p=1"))

    row = index.discovered_for(conn, sid)[0]

    assert row["oa_url"] == "https://x/?p=1", "the Read button's target"
    assert row["is_oa"] == 0, "not an open-access paper — no badge"


# ---- the subscription kind -------------------------------------------

def test_the_kind_is_accepted(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))

    sid = index.add_subscription(conn, "retraction_watch", "RW", "")

    assert [s["kind"] for s in index.list_subscriptions(conn)
            if s["id"] == sid] == ["retraction_watch"]


def test_an_invented_kind_is_still_refused(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))

    with pytest.raises(ValueError):
        index.add_subscription(conn, "retractionwatch", "RW", "")


def test_refresh_routes_to_the_blog(tmp_path, monkeypatch):
    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(conn, "retraction_watch", "RW", "")
    sub = [s for s in index.list_subscriptions(conn) if s["id"] == sid][0]
    monkeypatch.setattr(retractionwatch, "fetch_posts",
                        lambda **_k: [_post("https://x/?p=1")])
    # No DOI means nothing to ask Unpaywall about; make sure of it.
    from alexandria import metrics
    monkeypatch.setattr(metrics, "fetch_oa_locations",
                        lambda *a, **k: pytest.fail("no DOI to enrich"))

    fetched, new = feed.refresh_subscription(conn, sub)

    assert (fetched, new) == (1, 1)


def test_a_feed_that_cannot_be_reached_is_a_quiet_week(monkeypatch):
    """Not an error dialog: a subscription that fails to fetch should
    look like nothing was published."""
    def boom(*_a, **_k):
        raise OSError("network is unreachable")

    monkeypatch.setattr(retractionwatch.urllib.request, "urlopen", boom)

    assert retractionwatch.fetch_posts() == []
