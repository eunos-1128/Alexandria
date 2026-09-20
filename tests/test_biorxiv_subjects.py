"""Following bioRxiv subject collections.

bioRxiv has no search endpoint, so a subscription here is a *subject*
and you take what the collection sends. The traps, all measured
against the live feeds:

  * It is RSS 1.0 / RDF. The channel carries an `<items><rdf:Seq>`
    index of URIs before the real `<item>` elements, so a naive
    `<item>` match finds the index and yields empty titles.
  * `dc:creator` separates authors with a comma *and* separates each
    surname from its initials with a comma, so splitting on commas
    gives twice as many pieces as there are people.
  * DOIs arrive prefixed `doi:`, on bioRxiv's newer 10.64898 prefix.
  * A misspelt subject returns HTTP 200 with no items rather than a
    404 — which is why the UI offers a fixed list and never a text
    box.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import biorxiv

FEED = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/"
         xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
<channel rdf:about="https://biorxiv.org">
<title>bioRxiv Subject Collection: Biophysics</title>
<items>
<rdf:Seq>
<rdf:li rdf:resource="https://www.biorxiv.org/content/10.64898/x1v1?rss=1"/>
<rdf:li rdf:resource="https://www.biorxiv.org/content/10.64898/x2v1?rss=1"/>
</rdf:Seq>
</items>
</channel>
<item rdf:about="https://www.biorxiv.org/content/10.64898/x1v1?rss=1">
<title><![CDATA[ RfaH licenses RNA polymerase ]]></title>
<link>https://www.biorxiv.org/content/10.64898/x1v1?rss=1</link>
<description><![CDATA[ Processivity is essential for gene expression. ]]></description>
<dc:creator><![CDATA[ Buccolieri, L., Wang, B., Dulin, D. ]]></dc:creator>
<dc:date>2026-09-19</dc:date>
<dc:identifier>doi:10.64898/2026.09.18.752654</dc:identifier>
<dc:title><![CDATA[RfaH licenses RNA polymerase]]></dc:title>
</item>
<item rdf:about="https://www.biorxiv.org/content/10.64898/x2v1?rss=1">
<title><![CDATA[ No DOI here ]]></title>
<description><![CDATA[ Abstract. ]]></description>
<dc:creator><![CDATA[ Nobody, N. ]]></dc:creator>
<dc:date>2026-09-18</dc:date>
</item>
</rdf:RDF>
"""

EMPTY_FEED = b"""<?xml version="1.0" encoding="UTF-8" ?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/"
         xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
<channel rdf:about="https://biorxiv.org"><items><rdf:Seq/></items></channel>
</rdf:RDF>
"""


def test_the_seq_index_is_not_mistaken_for_items():
    rows = biorxiv.parse_feed(FEED)

    # Two <rdf:li> in the index, two <item>s, one of which has no DOI.
    assert len(rows) == 1
    assert rows[0]["title"] == "RfaH licenses RNA polymerase"


def test_the_doi_prefix_is_stripped():
    assert biorxiv.parse_feed(FEED)[0]["doi"] == "10.64898/2026.09.18.752654"


def test_an_item_without_a_doi_is_skipped():
    """`discovered` dedupes on DOI; a row without one cannot be
    stored and would arrive again on every refresh."""
    assert all(r["doi"] for r in biorxiv.parse_feed(FEED))


def test_authors_are_rebuilt_from_the_comma_soup():
    assert biorxiv.parse_feed(FEED)[0]["authors"] == [
        "L. Buccolieri", "B. Wang", "D. Dulin"]


@pytest.mark.parametrize("raw,expected", [
    ("Smith, J.", ["J. Smith"]),
    ("Smith, J. A., Jones, B.", ["J. A. Smith", "B. Jones"]),
    ("van der Waals, J. D., Curie, M.", ["J. D. van der Waals",
                                         "M. Curie"]),
    ("", []),
    (None, []),
])
def test_author_shapes(raw, expected):
    assert biorxiv._authors(raw) == expected


def test_the_row_is_complete_without_any_enrichment():
    """The whole argument for this source: one request gives a usable
    row, where the OpenAlex path needs a second call for the
    abstract."""
    row = biorxiv.parse_feed(FEED)[0]

    assert row["abstract"].startswith("Processivity is essential")
    assert row["published_date"] == "2026-09-19"
    assert row["year"] == 2026
    assert row["journal"] == "bioRxiv"
    assert row["is_oa"] is True


def test_an_empty_feed_is_empty_not_an_error():
    """What a misspelt subject looks like: 200, no items."""
    assert biorxiv.parse_feed(EMPTY_FEED) == []


def test_rubbish_is_not_an_exception():
    assert biorxiv.parse_feed(b"<not xml") == []
    assert biorxiv.parse_feed(b"") == []


def test_the_subject_list_is_the_measured_one():
    assert len(biorxiv.SUBJECTS) == 27
    assert "biophysics" in biorxiv.SUBJECTS
    assert all(s == s.lower() and "-" not in s for s in biorxiv.SUBJECTS), \
        "slugs are lower case with underscores; hyphens do not work"


def test_subject_labels_read_as_english():
    assert biorxiv.subject_label("cancer_biology") == "Cancer Biology"
    assert (biorxiv.subject_label("pharmacology_and_toxicology")
            == "Pharmacology and Toxicology")


def test_the_url_carries_the_subject():
    assert biorxiv.feed_url("plant_biology").endswith(
        "?subject=plant_biology")


def test_a_failing_fetch_is_empty():
    def boom(_url, timeout=None):
        raise OSError("no network")

    assert biorxiv.fetch_subject("biophysics", http_get=boom) == []


def test_fetch_passes_the_subject_through():
    seen = {}

    def fake(url, timeout=None):
        seen["url"] = url
        return FEED

    rows = biorxiv.fetch_subject("biophysics", http_get=fake)

    assert "subject=biophysics" in seen["url"]
    assert len(rows) == 1


# ---- as a subscription ----------------------------------------------

def test_the_refresher_stores_a_subjects_rows(tmp_path, monkeypatch):
    from alexandria import feed, index

    conn = index.open_db(str(tmp_path / "lib.db"))
    sid = index.add_subscription(
        conn, "biorxiv_subject", "bioRxiv: Biophysics", "biophysics")
    monkeypatch.setattr(biorxiv, "fetch_subject",
                        lambda slug, **k: biorxiv.parse_feed(FEED))
    # Unpaywall must not be consulted: the feed already said the
    # preprint is readable, and 30 lookups a refresh would be rude.
    monkeypatch.setattr(
        feed.__dict__.get("metrics", object), "fetch_oa_locations",
        lambda *a, **k: pytest.fail("no enrichment for preprints"),
        raising=False)

    sub = [s for s in index.list_subscriptions(conn) if s["id"] == sid][0]
    seen, new = feed.refresh_subscription(conn, sub)

    assert (seen, new) == (1, 1)
    rows = list(index.discovered_for(conn, sid, limit=10))
    assert rows[0]["doi"] == "10.64898/2026.09.18.752654"
    assert rows[0]["abstract"].startswith("Processivity")

    # Second pass: same feed, nothing new — the dedupe that stops a
    # quiet subject's months-long backlog arriving twice.
    assert feed.refresh_subscription(conn, sub)[1] == 0


# ---- the add-subscription UI ----------------------------------------

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Gtk, Adw
    _display_ok = bool(Gtk.init_check())
    if _display_ok:
        Adw.init()
except Exception:
    _display_ok = False

needs_display = pytest.mark.skipif(not _display_ok,
                                   reason="no display for GTK tests")


@pytest.fixture
def window(tmp_path, monkeypatch):
    from alexandria import feed_window, index

    conn = index.open_db(str(tmp_path / "lib.db"))
    # The new subscriptions fetch immediately in a thread; not here.
    monkeypatch.setattr(feed_window.FeedWindow, "_initial_fetch",
                        lambda self, sid: None)
    win = feed_window.FeedWindow(None, conn)
    yield win
    win.destroy()


@needs_display
def test_preprint_mode_offers_subjects_instead_of_a_text_box(window):
    """A misspelt subject answers 200 with no items, so it would look
    like a quiet week for ever. The list is fixed for that reason."""
    window._add_preprint_btn.set_active(True)

    assert window._subject_scroller.get_visible() is True
    assert window._add_entry.get_visible() is False
    assert len(window._subject_toggles) == 27

    window._add_topic_btn.set_active(True)
    assert window._subject_scroller.get_visible() is False
    assert window._add_entry.get_visible() is True


@needs_display
def test_each_ticked_subject_becomes_its_own_subscription(window):
    """One row per subject: `discovered` is keyed per subscription, so
    this is what keeps the subjects' feeds apart and lets a
    cross-listed preprint appear under both."""
    from alexandria import index

    window._add_preprint_btn.set_active(True)
    window._subject_toggles["biophysics"].set_active(True)
    window._subject_toggles["genomics"].set_active(True)

    window._do_add_subjects()

    subs = [s for s in index.list_subscriptions(window.conn)
            if s["kind"] == "biorxiv_subject"]
    assert sorted(s["query"] for s in subs) == ["biophysics", "genomics"]
    assert sorted(s["name"] for s in subs) == [
        "bioRxiv: Biophysics", "bioRxiv: Genomics"]
    # The ticks are cleared, so the panel does not re-offer what is
    # already followed.
    assert not any(t.get_active()
                   for t in window._subject_toggles.values())


@needs_display
def test_following_the_same_subject_twice_is_a_no_op(window):
    from alexandria import index

    window._add_preprint_btn.set_active(True)
    window._subject_toggles["ecology"].set_active(True)
    window._do_add_subjects()
    window._subject_toggles["ecology"].set_active(True)
    window._do_add_subjects()

    subs = [s for s in index.list_subscriptions(window.conn)
            if s["query"] == "ecology"]
    assert len(subs) == 1
    assert "Already following" in window._add_status.get_label()


@needs_display
def test_nothing_ticked_says_so(window):
    window._add_preprint_btn.set_active(True)

    window._do_add_subjects()

    assert "at least one" in window._add_status.get_label()
