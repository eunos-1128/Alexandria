"""bioRxiv subject feeds.

bioRxiv has no search endpoint. What it has is one RSS feed per
subject collection, so a subscription here is a *subject*, not a
query — you pick the collections you read and take what arrives. For
searching preprints by term, `europepmc.search_preprints` is the way
in; this is the standing-order half.

Measured against the live feeds on 2026-09-16 and 2026-09-19:

  * **It is RSS 1.0 / RDF, not RSS 2.0.** The channel carries an
    `<items><rdf:Seq>` index of resource URIs *before* the real
    `<item rdf:about="…">` elements. A naive `<item>` regex matches
    the index and yields empty titles, which is why this parses with
    ElementTree and namespaces.
  * **Every item carries a full abstract** — 30 of 30, mean ~1,600
    characters — plus title, `dc:creator`, `dc:date` and
    `dc:identifier`. So a row is complete from the feed alone, with
    no enrichment call. That is better than the OpenAlex path.
  * **DOIs carry a `doi:` prefix** to strip, and use bioRxiv's newer
    `10.64898/` prefix rather than `10.1101/`.
  * **Every subject returns exactly 30 items — a cap, not a window.**
    How much time those cover depends on the subject: 2 days for
    neuroscience, 37 for zoology, 159 for paleontology. Two
    consequences. The busiest subject subscribed to sets the useful
    poll interval, and the first poll of a quiet subject delivers
    months at once — so the dedupe in `discovered` has to be right or
    the first refresh floods the feed.
  * **A bogus subject returns HTTP 200 with no items, not a 404.** A
    typo therefore looks exactly like "nothing new this week", which
    is why `SUBJECTS` is a fixed list rather than a text box.
  * bioRxiv sends a malformed `Content-Type` (`application/xml;")`).
    Harmless to ElementTree; a client that validates the header would
    reject it.
"""

import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .identity import user_agent

FEED_URL = "https://connect.biorxiv.org/biorxiv_xml.php"

_RSS = "{http://purl.org/rss/1.0/}"
_DC = "{http://purl.org/dc/elements/1.1/}"

# The 27 collections, all verified to return 30 items (2026-09-16).
# Slugs are lower case with underscores; `+` and `%20` also work,
# hyphens do not.
SUBJECTS = (
    "animal_behavior_and_cognition",
    "biochemistry",
    "bioengineering",
    "bioinformatics",
    "biophysics",
    "cancer_biology",
    "cell_biology",
    "clinical_trials",
    "developmental_biology",
    "ecology",
    "epidemiology",
    "evolutionary_biology",
    "genetics",
    "genomics",
    "immunology",
    "microbiology",
    "molecular_biology",
    "neuroscience",
    "paleontology",
    "pathology",
    "pharmacology_and_toxicology",
    "physiology",
    "plant_biology",
    "scientific_communication_and_education",
    "synthetic_biology",
    "systems_biology",
    "zoology",
)

_SMALL_WORDS = {"and", "of", "the"}


def subject_label(slug):
    """"cancer_biology" → "Cancer biology", for a toggle button."""
    words = (slug or "").split("_")
    out = []
    for i, w in enumerate(words):
        out.append(w if (i and w in _SMALL_WORDS) else w.capitalize())
    return " ".join(out)


def feed_url(subject):
    return "{}?{}".format(
        FEED_URL, urllib.parse.urlencode({"subject": subject}))


def _http_get(url, timeout=30):
    req = urllib.request.Request(url, headers={"User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _text(node):
    return " ".join((node.text or "").split()) if node is not None else None


def _doi(raw):
    """`doi:10.64898/2026.09.18.752654` → `10.64898/2026.09.18.752654`."""
    if not raw:
        return None
    return re.sub(r"^\s*doi:\s*", "", raw, flags=re.IGNORECASE).strip() or None


def _authors(raw):
    """`"Buccolieri, L., Wang, B., Dulin, D."` → display names.

    The separator between authors is a comma and so is the separator
    inside each name, so splitting on commas alone gives twice as many
    pieces as there are people. Each piece that looks like initials is
    therefore glued back onto the surname before it, and the pair is
    flipped into reading order — which is the form every other feed
    row in the application uses."""
    if not raw:
        return []
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    initials = re.compile(r"^(?:[A-Z]\.?)(?:[-\s]?[A-Z]\.?)*$")
    out = []
    for part in parts:
        if out and initials.match(part):
            out[-1] = "{} {}".format(part, out[-1])
        else:
            out.append(part)
    return out


def parse_feed(xml_bytes):
    """Feed XML → article dicts of the shape `index.upsert_discovered`
    wants. Returns [] for anything unparseable, and skips items with
    no DOI, which is what the `discovered` table dedupes on."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    # `.//` and the RSS namespace together: the <rdf:Seq> index above
    # the items is made of <rdf:li>, so it cannot be matched here.
    for item in root.iter(_RSS + "item"):
        doi = _doi(_text(item.find(_DC + "identifier")))
        if not doi:
            continue
        date = _text(item.find(_DC + "date"))
        title = (_text(item.find(_DC + "title"))
                 or _text(item.find(_RSS + "title")))
        year = None
        if date and date[:4].isdigit():
            year = int(date[:4])
        out.append({
            "doi": doi,
            "title": title,
            "authors": _authors(_text(item.find(_DC + "creator"))),
            "abstract": _text(item.find(_RSS + "description")),
            # The server stands in for the journal, as it does for
            # Europe PMC preprint rows.
            "journal": "bioRxiv",
            "year": year,
            "published_date": date,
            # bioRxiv preprints are free to read; the PDF is
            # constructed by pdf_fetch rather than advertised here.
            "is_oa": True,
            "oa_status": "green",
            "oa_url": _text(item.find(_RSS + "link")),
        })
    return out


def fetch_subject(subject, http_get=None, timeout=30):
    """Articles currently listed for one subject, newest first.

    Raises nothing: a feed that fails is empty, which the caller has
    to handle anyway. An *empty* result is ambiguous by bioRxiv's
    design — a misspelt subject answers 200 with no items — so callers
    that accept arbitrary subjects should check against `SUBJECTS`."""
    get = http_get or _http_get
    try:
        return parse_feed(get(feed_url(subject), timeout=timeout))
    except TypeError:
        try:
            return parse_feed(get(feed_url(subject)))
        except Exception:
            return []
    except Exception:
        return []
