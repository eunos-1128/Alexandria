"""Who paid for the work — aggregated from the papers you hold.

OpenAlex puts `funders` and `grants` (funder + award_id) on a work
record, and `importer` has been storing both in the sidecar since the
CrossRef-extras backfill: 117 of 199 papers in the reference library
carry funders, 100 carry award numbers. Per paper they already show
as a Funded-by chip. This aggregates them per author.

**What this is not.** It is the funding on *the papers you happen to
hold*, which is your reading list rather than anybody's portfolio.
And funder attribution in a work record belongs to the paper, not to
a named author: on a thirty-author consortium paper every co-author
inherits every grant. So this answers "which funders appear on the
papers I have by this person", and the UI says so in as many words.
The real question — which grants this PI holds — needs an award
database that records the award's own PI (UKRI GtR, NIH RePORTER),
which is the larger item this one sits underneath.

GTK-free, so the shape of the answer can be tested without a window.
"""

import json
import re
import unicodedata

# Funder names arrive from CrossRef via OpenAlex and are entered by
# hand at deposit time, so the same body appears under several
# spellings: curly versus straight apostrophes, "The" prefixes,
# trailing punctuation. Normalising only for *grouping* — the label
# shown is the most common spelling actually seen.
_PUNCT = re.compile(r"[^\w\s]", re.UNICODE)
_WS = re.compile(r"\s+")


def _key(name):
    """Grouping key for a funder name.

    Accents are folded as well as punctuation, because the same body
    is deposited both ways: the library holds "Ministero
    dell'Istruzione, dell'Università…" and "…dell'Universita…" as
    two records of one ministry."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name.strip().lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.replace("’", "'").replace("‘", "'")
    if s.startswith("the "):
        s = s[4:]
    s = _PUNCT.sub(" ", s)
    return _WS.sub(" ", s).strip()


def _authorships(paper):
    """The paper's authorships, whether it arrives as a sidecar dict
    or an index row with JSON in a column."""
    raw = paper.get("authorships") or paper.get("authorships_json")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            return []
    return raw or []


def paper_has_author(paper, name=None, openalex_id=None, orcid=None):
    """Whether `paper` lists this author.

    Identifier first, name only as a fallback: two people share a
    name more often than they share an OpenAlex ID."""
    for a in _authorships(paper):
        if not isinstance(a, dict):
            continue
        if openalex_id and a.get("openalex_id") == openalex_id:
            return True
        if orcid and a.get("orcid") == orcid:
            return True
    if openalex_id or orcid:
        return False
    if not name:
        return False
    return any(isinstance(a, dict) and a.get("name") == name
               for a in _authorships(paper))


def _funders_of(paper):
    """Funder names on a paper, from `funders` and from `grants`.

    Both are read because they disagree: a grant can name a funder
    that never made it into the `funders` list, and vice versa."""
    names = []
    for f in paper.get("funders") or []:
        if isinstance(f, str) and f.strip():
            names.append(f.strip())
    for g in paper.get("grants") or []:
        if isinstance(g, dict) and (g.get("funder") or "").strip():
            names.append(g["funder"].strip())
    return names


def profile(papers, name=None, openalex_id=None, orcid=None):
    """Funder profile for one author across `papers`.

    Returns `[{funder, n_papers, awards, first_year, last_year}, …]`,
    most papers first and then alphabetically, so the order is stable
    for a given library rather than shifting between two funders that
    tie."""
    groups = {}
    for paper in papers or []:
        if not paper_has_author(paper, name, openalex_id, orcid):
            continue
        seen_here = set()
        for raw_name in _funders_of(paper):
            k = _key(raw_name)
            if not k:
                continue
            g = groups.setdefault(k, {
                "spellings": {}, "n_papers": 0, "awards": set(),
                "years": [],
            })
            g["spellings"][raw_name] = g["spellings"].get(raw_name, 0) + 1
            if k not in seen_here:
                # One paper counts once for a funder, however many of
                # its grants name that funder.
                g["n_papers"] += 1
                seen_here.add(k)
                year = paper.get("year")
                if isinstance(year, int) and year > 0:
                    g["years"].append(year)
        for grant in paper.get("grants") or []:
            if not isinstance(grant, dict):
                continue
            k = _key(grant.get("funder"))
            award = (grant.get("award_id") or "").strip()
            if k in groups and award:
                groups[k]["awards"].add(award)

    out = []
    for g in groups.values():
        label = max(g["spellings"].items(), key=lambda kv: kv[1])[0]
        out.append({
            "funder": label,
            "n_papers": g["n_papers"],
            "awards": sorted(g["awards"]),
            "first_year": min(g["years"]) if g["years"] else None,
            "last_year": max(g["years"]) if g["years"] else None,
        })
    out.sort(key=lambda r: (-r["n_papers"], r["funder"].lower()))
    return out


def summarise(entry):
    """One line for a funder row: the years it spans and how many
    papers.

    Award numbers are deliberately absent. They are the least
    reliable part of the record — measured over this library, 13 of
    the 85 papers carrying two or more awards attach at least one to
    the wrong funder, and some award fields are not numbers at all
    ("2020 '"). Putting them in the row would give the shakiest
    figure the most prominence; the caller offers them on hover,
    where a reader has asked."""
    bits = []
    first, last = entry.get("first_year"), entry.get("last_year")
    if first and last:
        bits.append(str(first) if first == last
                    else "{}–{}".format(first, last))
    n = entry.get("n_papers") or 0
    bits.append("{} paper{}".format(n, "" if n == 1 else "s"))
    return " · ".join(bits)


def papers_from_index(conn):
    """Every paper in the catalogue, as dicts this module can read.

    The sidecar is the source of `funders`/`grants`; the index row
    carries `authorships_json` and the year. Reading the sidecars
    would mean 200 file opens per author page, so the funding columns
    are taken from the index where they exist and the sidecar is left
    alone."""
    rows = []
    try:
        cur = conn.execute(
            "SELECT title, year, authorships_json, funders_json,"
            " grants_json FROM papers")
    except Exception:
        return rows
    for r in cur:
        d = {"title": r["title"], "year": r["year"],
             "authorships_json": r["authorships_json"]}
        for src, dest in (("funders_json", "funders"),
                          ("grants_json", "grants")):
            try:
                d[dest] = json.loads(r[src]) if r[src] else []
            except Exception:
                d[dest] = []
        rows.append(d)
    return rows
