"""UKRI Gateway to Research: the grants a person actually holds.

OpenAlex tells you which funders appear on a paper. It cannot tell
you whose grant it was — funder attribution belongs to the work, so
every co-author of a consortium paper inherits all of it. GtR records
the award itself: who was Principal Investigator, who was Co-I, the
reference, the amount in pounds and the dates.

Two things make it usable where the backlog feared it would not be:

  * **the person record carries an ORCID**, so matching a paper's
    author to a funded researcher is an identifier lookup rather than
    a guess from name and institution;
  * **PI and Co-I are distinct link types** (`PI_PER`, `COI_PER`),
    which is exactly the distinction the paper-side data lacks.

UK-only, by construction. It covers the seven research councils plus
Innovate UK and the NIHR partnerships, and nothing else — an
American PI simply is not in it, which the caller has to present as
"not found here" rather than "no grants".

HTTP is injected (`http_get_json`) so the parsing can be tested
without a network, in the same shape `author_image` uses for
Wikidata.
"""

import json
import urllib.parse
import urllib.request

from .identity import user_agent

API = "https://gtr.ukri.org/gtr/api/"

# GtR negotiates by version; v7 is the current documented one and
# returns the link relations this module depends on.
_ACCEPT = "application/vnd.rcuk.gtr.json-v7"

# The link relations that say what someone did on a project.
ROLE_PI = "PI_PER"
ROLE_COI = "COI_PER"
_ROLE_LABELS = {ROLE_PI: "Principal Investigator", ROLE_COI: "Co-Investigator"}


def _http_get_json(url, timeout=25):
    req = urllib.request.Request(
        url, headers={"Accept": _ACCEPT, "User-Agent": user_agent()})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def _https(href):
    """GtR returns `http://` hrefs in its own payloads."""
    if href and href.startswith("http://"):
        return "https://" + href[len("http://"):]
    return href


def _persons_url(query, field=None, size=20):
    """`fetchSize`/`page`, not `s`/`p` — the short forms return HTTP
    400, which is a plausible way for an earlier attempt at this API
    to have died."""
    params = {"q": query, "fetchSize": size}
    if field:
        params["f"] = field
    return API + "persons?" + urllib.parse.urlencode(params)


def orcid_search_url(orcid):
    """Exact lookup by ORCID — one request, one answer, no guessing."""
    return _persons_url(orcid, field="per.orcidId", size=5)


def surname_search_url(surname, size=20):
    """Search the surname *field*.

    Unscoped `q=` is free text over the whole record, which is close
    to useless for a person: "Cowtan" returns Jon Agirre and Keith
    Wilson (their projects mention him), and "Read" returns twenty
    unrelated people because the word appears in every abstract.
    Scoping to `per.sn` turns that into one hit."""
    return _persons_url(surname, field="per.sn", size=size)


def _links(record, rel=None):
    links = ((record or {}).get("links") or {}).get("link") or []
    if rel is None:
        return links
    return [l for l in links if (l or {}).get("rel") == rel]


def person_orcid(person):
    """The ORCID on a GtR person record, bare (no URL), or None."""
    for l in _links(person, "ORCID_ID"):
        href = l.get("href") or ""
        tail = href.rstrip("/").rsplit("/", 1)[-1]
        if tail:
            return tail
    return None


def person_projects(person):
    """`[(role, project_url), …]` for the roles we report on."""
    out = []
    for rel in (ROLE_PI, ROLE_COI):
        for l in _links(person, rel):
            href = _https(l.get("href"))
            if href:
                out.append((rel, href))
    return out


def match_person(persons, orcid=None, name=None):
    """`(person, confidence)` — confidence is "orcid", "name" or None.

    An ORCID match is taken wherever one is available on both sides.
    Without it, a single exact full-name hit is accepted and labelled
    as such, so the UI can say how it was matched; anything ambiguous
    returns None rather than picking. Attributing someone else's
    grants to a person is a worse failure than finding nothing."""
    people = persons or []
    if orcid:
        for p in people:
            if person_orcid(p) == orcid:
                return p, "orcid"
        # An ORCID was available and nothing matched it. Do not fall
        # back to the name: the identifier has already said no.
        return None, None
    if not name:
        return None, None
    wanted = " ".join(name.split()).lower()
    hits = [p for p in people
            if " ".join("{} {}".format(p.get("firstName") or "",
                                       p.get("surname") or "").split()).lower()
            == wanted]
    if len(hits) == 1:
        return hits[0], "name"
    return None, None


def parse_project(project, fund=None):
    """One grant, flattened: title, funder, reference, amount, dates.

    `fund` is the record behind the project's FUND link, which is
    where the money lives — the project's own `fund` field is null in
    every record examined."""
    ident = None
    for i in ((project or {}).get("identifiers") or {}).get("identifier", []):
        if (i or {}).get("value"):
            ident = i["value"]
            break
    value = ((fund or {}).get("valuePounds") or {}).get("amount")
    return {
        "title": (project or {}).get("title"),
        "funder": (project or {}).get("leadFunder"),
        "category": (project or {}).get("grantCategory"),
        "status": (project or {}).get("status"),
        "reference": ident,
        "amount_gbp": value if isinstance(value, int) else None,
        "start": _epoch_year((fund or {}).get("start")),
        "end": _epoch_year((fund or {}).get("end")),
        "url": _https((project or {}).get("href")),
    }


def _epoch_year(ms):
    """GtR dates are epoch milliseconds; the year is all we show."""
    if not isinstance(ms, (int, float)):
        return None
    import datetime
    try:
        return datetime.datetime.fromtimestamp(
            ms / 1000.0, datetime.timezone.utc).year
    except (ValueError, OSError, OverflowError):
        return None


def fund_url(project):
    for l in _links(project, "FUND"):
        href = _https(l.get("href"))
        if href:
            return href
    return None


def grants_for_author(authorship, http_get_json=None, max_projects=25):
    """Every UKRI grant GtR records for this author.

    Returns `{matched, confidence, orcid, grants: [...]}`. `matched`
    is False when GtR has nobody of that name, or has people but none
    that the identifier will vouch for — the caller should say "no
    UKRI record", never "no funding": most of the world's researchers
    are not in this database at all.
    """
    get = http_get_json or _http_get_json
    name = (authorship or {}).get("name") or ""
    surname = name.split()[-1] if name.split() else ""
    out = {"matched": False, "confidence": None,
           "orcid": (authorship or {}).get("orcid"), "grants": []}
    orcid = (authorship or {}).get("orcid")
    person = confidence = None
    if orcid:
        # Exact identifier lookup: one request, and an answer that
        # needs no corroboration.
        try:
            data = get(orcid_search_url(orcid))
            person, confidence = match_person(
                (data or {}).get("person") or [], orcid=orcid)
        except Exception:
            person = None
    # An ORCID search that returns nothing is not a contradiction —
    # GtR's ORCIDs are self-declared and often absent (Kevin Cowtan
    # has one in OpenAlex and none here). Falling back to an exact
    # full-name match on the surname *field* costs nothing in
    # precision, since a wrong name is still refused.
    if person is None and surname:
        try:
            data = get(surname_search_url(surname))
            person, confidence = match_person(
                (data or {}).get("person") or [], name=name)
        except Exception:
            person = None
    if person is None:
        return out
    out["matched"] = True
    out["confidence"] = confidence
    out["orcid"] = person_orcid(person) or out["orcid"]
    for role, url in person_projects(person)[:max_projects]:
        try:
            project = get(url)
            fund = None
            furl = fund_url(project)
            if furl:
                fund = get(furl)
        except Exception:
            continue
        grant = parse_project(project, fund)
        grant["role"] = _ROLE_LABELS.get(role, role)
        grant["is_pi"] = (role == ROLE_PI)
        out["grants"].append(grant)
    out["grants"].sort(
        key=lambda g: (0 if g.get("is_pi") else 1,
                       -(g.get("start") or 0)))
    return out


def summarise_grant(grant):
    """One line under a grant title: funder, reference, money, years."""
    bits = []
    if grant.get("funder"):
        bits.append(grant["funder"])
    if grant.get("reference"):
        bits.append(grant["reference"])
    if grant.get("amount_gbp"):
        bits.append("£{:,}".format(grant["amount_gbp"]))
    start, end = grant.get("start"), grant.get("end")
    if start and end:
        bits.append(str(start) if start == end
                    else "{}–{}".format(start, end))
    elif start:
        bits.append(str(start))
    return " · ".join(bits)


def total_awarded(grants, pi_only=True):
    """Sum of award values. PI-only by default: a Co-I line is the
    whole project's value, not this person's share, so adding those
    in would inflate the figure by other people's money."""
    return sum(g.get("amount_gbp") or 0 for g in grants or []
               if g.get("amount_gbp") and (g.get("is_pi") or not pi_only))
