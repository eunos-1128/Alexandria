"""Europe PMC preprint search.

OpenAlex is late on preprints — often by weeks, sometimes entirely —
which is the gap this fills. Europe PMC indexes bioRxiv and medRxiv
directly, needs no key, and one request returns most of a Discover
row: title, abstract, structured authors, DOI and date.

Two things learned the hard way, both measured against the live API:

  * **`resultType=core` is the whole trick.** The default `lite`
    returns identifiers and a flat `authorString` and no abstract at
    all. `core` adds `abstractText` (~1,300 characters on a typical
    preprint), an `authorList`, and `fullTextUrlList`. The response
    grows from 4 KB to 15 KB for five results, which is a fair price
    for the difference between a stub and a usable row.

  * **Its own reachability flags understate badly.** Every preprint
    measured reports `hasPDF: "N"` and `isOpenAccess: "N"`, and the
    PDFs download regardless — see `pdf_fetch._biorxiv_pdf_urls`.
    Those flags describe what Europe PMC *holds*, not what exists on
    the open web, so nothing here filters on them.

HTTP is injected so the query building and parsing can be tested
without a network.
"""

import json
import urllib.parse
import urllib.request

from .identity import user_agent

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"

# `SRC:PPR` is Europe PMC's preprint corpus. Without it the same
# search returns published papers, which OpenAlex already covers
# better.
PREPRINT_SOURCE = "SRC:PPR"

# The servers worth offering. Europe PMC indexes others, but these
# are the two that matter for the life sciences and the two whose
# PDFs `pdf_fetch` knows how to construct.
PUBLISHERS = ("bioRxiv", "medRxiv")


def _http_get_json(url, timeout=30):
    req = urllib.request.Request(
        url, headers={"User-Agent": user_agent(),
                      "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _terms(text):
    """Split a query box into search terms, honouring quotes.

    `cryo-EM "model building"` becomes two terms, the second phrase
    kept whole — otherwise a two-word phrase silently becomes two
    unrelated requirements and the result set collapses."""
    out, buf, in_quotes = [], [], False
    for ch in text or "":
        if ch == '"':
            if in_quotes and buf:
                out.append("".join(buf).strip())
                buf = []
            in_quotes = not in_quotes
        elif ch.isspace() and not in_quotes:
            if buf:
                out.append("".join(buf).strip())
                buf = []
        else:
            buf.append(ch)
    if buf:
        out.append("".join(buf).strip())
    return [t for t in out if t]


def build_query(text, publisher=None, since=None, until=None):
    """The Europe PMC query string for a preprint search.

    Terms are ANDed against the abstract rather than the whole
    record: a preprint's full text is indexed too, and searching it
    returns papers that merely mention the words in passing."""
    parts = ["({})".format(PREPRINT_SOURCE)]
    if publisher:
        parts.append('(PUBLISHER:"{}")'.format(publisher))
    terms = _terms(text)
    if terms:
        parts.append("({})".format(
            " AND ".join('ABSTRACT:"{}"'.format(t.replace('"', ""))
                         for t in terms)))
    if since or until:
        parts.append("(FIRST_PDATE:[{} TO {}])".format(
            since or "1900-01-01", until or "2100-12-31"))
    return " AND ".join(parts)


def search_url(text, publisher=None, since=None, until=None, limit=25):
    return SEARCH_URL + "?" + urllib.parse.urlencode({
        "query": build_query(text, publisher, since, until),
        "format": "json",
        "pageSize": max(1, min(int(limit), 100)),
        "resultType": "core",
        "sort": "P_PDATE_D desc",
    })


def _authors(result):
    """`(first, last)` display names.

    `authorList` is the structured form and is what `core` buys;
    `authorString` is the flat fallback ("Naskalska A, Biela A, …")
    for the occasional record without one."""
    people = ((result.get("authorList") or {}).get("author")) or []
    names = [p.get("fullName") or p.get("firstName") or ""
             for p in people if isinstance(p, dict)]
    names = [n for n in names if n]
    if not names:
        flat = (result.get("authorString") or "").strip().rstrip(".")
        names = [n.strip() for n in flat.split(",") if n.strip()]
    if not names:
        return None, None
    return names[0], (names[-1] if len(names) > 1 else None)


def parse_result(result):
    """One Europe PMC record as a Discover row."""
    first, last = _authors(result)
    date = (result.get("firstPublicationDate")
            or result.get("pubYear") or "")
    year = None
    if date[:4].isdigit():
        year = int(date[:4])
    return {
        "doi": result.get("doi"),
        "title": result.get("title"),
        "first_author": first,
        "last_author": last,
        # The server stands in for the journal: it is what a reader
        # needs to know about a preprint, and the row already has a
        # place to put it. Europe PMC files the server name under
        # `bookOrReportDetails`, which is an odd home for a preprint
        # and the only place it appears.
        "journal": (((result.get("bookOrReportDetails") or {})
                     .get("publisher")) or "Preprint"),
        "year": year,
        "publication_date": date if len(date) == 10 else None,
        "abstract": result.get("abstractText"),
        # Preprints carry no citation count worth showing; the row
        # hides the line when this is falsy.
        "citations": 0,
        "source_id": result.get("id"),
    }


def search_preprints(text, publisher=None, since=None, until=None,
                     limit=25, http_get_json=None):
    """`[row, …]` newest first, or `[]` when Europe PMC says nothing.

    Raises nothing: a search that fails is empty, which is what the
    caller has to handle anyway."""
    get = http_get_json or _http_get_json
    try:
        data = get(search_url(text, publisher, since, until, limit))
    except Exception:
        return []
    results = ((data or {}).get("resultList") or {}).get("result") or []
    return [parse_result(r) for r in results if isinstance(r, dict)]
