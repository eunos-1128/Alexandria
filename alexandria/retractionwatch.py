"""Retraction Watch — the blog, as a subscription.

retractionwatch.com reports on retractions, fraud, paper mills and
the machinery of correcting the literature. This module follows its
RSS feed so the reporting arrives beside the journals and preprint
subjects in the feed window.

What it is *not* is a check on the reader's own library. Measured
against the live feed on 2026-09-26:

  * The feed is standard WordPress RSS 2.0 at `/feed/`, HTTP 200,
    `application/rss+xml`, about 114 KB.
  * It holds **10 items** — a rolling window, like the bioRxiv
    subject feeds, so a quiet fortnight and a missed fortnight look
    the same. Poll often enough and nothing is lost.
  * Across the whole 114 KB there are **nine DOI-shaped strings**,
    most of them inside prose. So these posts cannot be matched to
    papers, and none of them is filed as one. That job belongs to
    the Retraction Watch *database*, which Crossref publishes as a
    CSV keyed on the retracted paper's DOI — see the backlog.
  * `<guid isPermaLink="false">` is a stable post id
    (`https://retractionwatch.com/?p=136091`) and is what dedupe
    keys on. `<link>` is the readable permalink and would change if
    a post were re-slugged.
  * `<description>` is a teaser with a "Continue reading …" anchor
    welded to the end, which has to come off or every card ends
    with the same nine words.
  * `/category/retractions/feed/` exists but came back effectively
    empty (1.2 KB), so the main feed is the only useful one.
"""

import email.utils
import html
import re
import urllib.request
import xml.etree.ElementTree as ET

FEED_URL = "https://retractionwatch.com/feed/"

# The name a subscription to this gets. Fixed: there is one feed, so
# unlike a journal or a subject there is nothing to parameterise.
SUBSCRIPTION_NAME = "Retraction Watch"

_NS = {
    "dc": "http://purl.org/dc/elements/1.1/",
    "content": "http://purl.org/rss/1.0/modules/content/",
}

# The teaser's tail: "… <a …>Continue reading <span>title</span></a>".
_CONTINUE_RE = re.compile(
    r"\s*(?:&#8230;|…|\.\.\.)?\s*<a\b[^>]*>\s*Continue reading.*$",
    re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")

# The post's lead image. Every item in the feed of 2026-09-26 had
# one, as the first <img> inside `content:encoded` — there is no
# <media:content>, <media:thumbnail> or <enclosure> to ask instead.
_IMG_RE = re.compile(r"<img\b[^>]*?\bsrc=[\"']([^\"']+)[\"']", re.I)
# WordPress serves a ladder of resized copies of every upload:
# `name-1024x683.jpg` beside `name-300x200.jpg` and the original.
# A card wants a thumbnail, and 1024 px of JPEG per row is a waste
# of somebody's bandwidth on both ends.
_WP_SIZE_RE = re.compile(r"-(\d{3,4})x(\d{3,4})(\.[A-Za-z0-9]+)$")
_THUMB_WIDTH = 300


def _text(node, path, ns=None):
    found = node.find(path, ns or {})
    if found is None:
        return None
    return (found.text or "").strip() or None


def _clean_summary(raw):
    """The teaser as prose: no markup, no "Continue reading" tail,
    entities resolved."""
    if not raw:
        return None
    out = _CONTINUE_RE.sub("", raw)
    out = _TAG_RE.sub(" ", out)
    out = html.unescape(out)
    out = _WS_RE.sub(" ", out).strip()
    # A stray ellipsis left where the anchor was reads as a typo.
    out = re.sub(r"\s*[…]\s*$", "…", out)
    return out or None


def _smaller_wordpress_copy(url):
    """The 300 px-wide sibling of a WordPress upload, where the URL
    says which size it is.

    `…/iStock-2207141986-2-1024x683.jpg` → `…-300x200.jpg`, keeping
    the aspect ratio WordPress itself used. Returned only when the
    arithmetic is safe; anything unrecognised is handed back
    untouched, and a wrong guess costs a 404 rather than a wrong
    picture."""
    m = _WP_SIZE_RE.search(url or "")
    if not m:
        return url
    w, h, ext = int(m.group(1)), int(m.group(2)), m.group(3)
    if w <= _THUMB_WIDTH or not w:
        return url
    new_h = max(1, round(h * _THUMB_WIDTH / w))
    return url[:m.start()] + "-{}x{}{}".format(_THUMB_WIDTH, new_h, ext)


def _lead_image(item):
    """URL of the post's lead image, or None.

    Looks in `content:encoded` first — that is the post body, where
    the hero image lives — then the teaser, which sometimes repeats
    it."""
    for path, ns in (("content:encoded", _NS), ("description", None)):
        node = item.find(path, ns or {})
        if node is None or not node.text:
            continue
        m = _IMG_RE.search(node.text)
        if m:
            src = html.unescape(m.group(1)).strip()
            if src.startswith(("http://", "https://")):
                return src
    return None


def _iso_date(pub_date):
    """RFC 822 `Fri, 25 Sep 2026 15:01:12 +0000` → `2026-09-25`.

    Date only: the feed window sorts on `published_date` as a string
    alongside rows from CrossRef and OpenAlex, which are dates."""
    if not pub_date:
        return None
    try:
        return email.utils.parsedate_to_datetime(pub_date).date().isoformat()
    except (TypeError, ValueError):
        return None


def parse_feed(xml_text):
    """Posts from the feed XML, newest first, as `discovered`-shaped
    dicts.

    No `doi` — these are articles about papers, not papers — so each
    carries `source_url`, which is what `index.upsert_discovered`
    dedupes on when there is no DOI."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for item in root.iter("item"):
        title = _text(item, "title")
        link = _text(item, "link")
        guid = _text(item, "guid")
        if not title or not (guid or link):
            continue
        creator = _text(item, "dc:creator", _NS)
        categories = [c.text.strip() for c in item.findall("category")
                      if (c.text or "").strip()]
        raw_desc = item.find("description")
        summary = _clean_summary(raw_desc.text if raw_desc is not None
                                 else None)
        out.append({
            "title": title,
            # Dedupe key: the guid, which survives a re-slug.
            "source_url": guid or link,
            # Where "Read" goes: the human permalink.
            "url": link or guid,
            "published_date": _iso_date(_text(item, "pubDate")),
            "authors": [creator] if creator else [],
            "abstract": summary,
            # The post's own tags — "paper mills", "wiley", "legal
            # threats" — shown as the row's journal slot, which is
            # otherwise empty for a blog post.
            "journal": ", ".join(categories[:3]) or None,
            "doi": None,
            "image_url": _smaller_wordpress_copy(_lead_image(item)),
        })
    return out


def fetch_posts(limit=30, timeout=20):
    """The current feed, or [] on any failure — a subscription that
    cannot be reached is a quiet week, not an error."""
    req = urllib.request.Request(
        FEED_URL, headers={"User-Agent": "alexandria",
                           "Accept": "application/rss+xml, text/xml"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except Exception:
        return []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        text = raw.decode("utf-8", errors="replace")
    return parse_feed(text)[:limit]
