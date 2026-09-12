"""Gateway to Research: the grants a person actually holds.

The paper-side data cannot answer "whose grant was this" — funder
attribution belongs to the work, so every co-author of a consortium
paper inherits all of it. GtR records the award: PI, Co-I, reference,
amount, dates.

Two findings from the reconnaissance (2026-09-10) shape this module,
and both are pinned here so a later change cannot quietly undo them:
unscoped `q=` is free text over the whole record, and the person
record carries an ORCID.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import gtr


def person(first, last, orcid=None, pi=0, coi=0):
    links = []
    if orcid:
        links.append({"rel": "ORCID_ID",
                      "href": "http://orcid.org/" + orcid})
    for i in range(pi):
        links.append({"rel": "PI_PER",
                      "href": "http://gtr.ukri.org/gtr/api/projects/P%d" % i})
    for i in range(coi):
        links.append({"rel": "COI_PER",
                      "href": "http://gtr.ukri.org/gtr/api/projects/C%d" % i})
    return {"firstName": first, "surname": last,
            "links": {"link": links}}


PROJECT = {
    "title": "CCP4 Grant Renewal 2014-2019",
    "leadFunder": "BBSRC",
    "grantCategory": "Research Grant",
    "status": "Closed",
    "href": "http://gtr.ukri.org/gtr/api/projects/4F79",
    "identifiers": {"identifier": [{"type": "RCUK", "value": "BB/L007010/1"}]},
    "links": {"link": [{"rel": "FUND",
                        "href": "http://gtr.ukri.org/gtr/api/funds/2379"}]},
}
FUND = {"valuePounds": {"amount": 339104, "currencyCode": "GBP"},
        "start": 1405292400000, "end": 1562972400000}


# --- the two API traps ----------------------------------------------

def test_person_search_is_scoped_to_a_field():
    """Unscoped free text is useless for a person: "Cowtan" returned
    Jon Agirre and Keith Wilson (their projects mention him) and
    "Read" returned twenty strangers."""
    assert "f=per.sn" in gtr.surname_search_url("Cowtan")
    assert "f=per.orcidId" in gtr.orcid_search_url("0000-1")


def test_paging_uses_the_long_parameter_names():
    """`s`/`p` return HTTP 400 — the likely grave of an earlier
    attempt at this API."""
    url = gtr.surname_search_url("Murshudov", size=5)
    assert "fetchSize=5" in url
    assert "&s=" not in url and "&p=" not in url


# --- matching --------------------------------------------------------

def test_orcid_wins():
    people = [person("Someone", "Else", orcid="0000-9"),
              person("Garib", "Murshudov", orcid="0000-1")]

    p, how = gtr.match_person(people, orcid="0000-1")

    assert p["surname"] == "Murshudov" and how == "orcid"


def test_an_exact_name_is_accepted_when_there_is_no_orcid():
    people = [person("Kevin", "Cowtan")]

    p, how = gtr.match_person(people, name="Kevin Cowtan")

    assert p is not None and how == "name"


def test_an_ambiguous_name_is_refused():
    """Attributing someone else's grants is worse than finding
    nothing."""
    people = [person("Kevin", "Cowtan"), person("Kevin", "Cowtan")]

    assert gtr.match_person(people, name="Kevin Cowtan") == (None, None)


def test_a_near_miss_name_is_refused():
    people = [person("Kathryn", "Cowtan")]

    assert gtr.match_person(people, name="Kevin Cowtan") == (None, None)


def test_orcid_present_but_unmatched_does_not_fall_back_to_the_name():
    """Within one result set the identifier has already spoken."""
    people = [person("Kevin", "Cowtan", orcid="0000-OTHER")]

    assert gtr.match_person(
        people, orcid="0000-1", name="Kevin Cowtan") == (None, None)


# --- parsing ---------------------------------------------------------

def test_a_project_flattens_to_the_things_worth_showing():
    g = gtr.parse_project(PROJECT, FUND)

    assert g["funder"] == "BBSRC"
    assert g["reference"] == "BB/L007010/1"
    assert g["amount_gbp"] == 339104
    assert (g["start"], g["end"]) == (2014, 2019)


def test_the_money_comes_from_the_fund_record():
    """The project's own `fund` field is null in every record seen;
    the value lives behind the FUND link."""
    g = gtr.parse_project(PROJECT, None)

    assert g["amount_gbp"] is None
    assert gtr.fund_url(PROJECT).endswith("/funds/2379")
    assert gtr.fund_url(PROJECT).startswith("https://")


def test_summary_line():
    g = gtr.parse_project(PROJECT, FUND)

    assert gtr.summarise_grant(g) == \
        "BBSRC · BB/L007010/1 · £339,104 · 2014–2019"


def test_orcid_is_read_off_the_link():
    assert gtr.person_orcid(person("A", "B", orcid="0000-1")) == "0000-1"
    assert gtr.person_orcid(person("A", "B")) is None


def test_totals_count_only_the_grants_this_person_led():
    """A Co-I line carries the whole project's value, not a share of
    it; adding those in would report other people's money."""
    grants = [{"amount_gbp": 100, "is_pi": True},
              {"amount_gbp": 900, "is_pi": False}]

    assert gtr.total_awarded(grants) == 100
    assert gtr.total_awarded(grants, pi_only=False) == 1000


# --- the whole lookup, without a network -----------------------------

def test_grants_for_author_end_to_end():
    pages = {
        gtr.orcid_search_url("0000-1"): {
            "person": [person("Garib", "Murshudov", orcid="0000-1", pi=1)]},
        "https://gtr.ukri.org/gtr/api/projects/P0": PROJECT,
        "https://gtr.ukri.org/gtr/api/funds/2379": FUND,
    }

    out = gtr.grants_for_author({"name": "Garib Murshudov",
                                 "orcid": "0000-1"},
                                http_get_json=lambda url: pages[url])

    assert out["matched"] and out["confidence"] == "orcid"
    assert len(out["grants"]) == 1
    assert out["grants"][0]["is_pi"] is True
    assert out["grants"][0]["role"] == "Principal Investigator"


def test_an_author_gtr_has_never_heard_of():
    """Most researchers are not UK-funded. The caller must be able to
    say "no UKRI record", not "no funding"."""
    out = gtr.grants_for_author(
        {"name": "David Baker", "orcid": "0000-X"},
        http_get_json=lambda url: {"person": []})

    assert out["matched"] is False
    assert out["grants"] == []


def test_a_network_failure_is_not_an_exception():
    def boom(url):
        raise OSError("gtr is down")

    out = gtr.grants_for_author({"name": "Someone", "orcid": "0000-1"},
                                http_get_json=boom)

    assert out["matched"] is False
