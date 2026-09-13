"""Which OpenAlex author record a profile comes from.

Reported 2026-09-13 from a screenshot: an author page reading "2 works
· h-index 0" above a correct, full list of that author's papers. Two
OpenAlex author records carry the same ORCID — `A5138223712`, created
2026-06-10, with 2 works and 0 citations, and `A5019985343` with 175
works and 82,591 citations — and `/authors/orcid:…` resolves to the
stub. Everything else on the page filters *works* by
`author.orcid:…`, which is identity-agnostic and so stayed right; only
the profile call resolves an entity.

The stored OpenAlex ID therefore wins, because it came from the
authorships on papers in the library — the record that owns the work.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import metrics

ORCID = "0000-0001-8781-9753"
STUB = {"id": "https://openalex.org/A5138223712",
        "display_name": "Martin Steinegger",
        "orcid": "https://orcid.org/" + ORCID,
        "works_count": 2, "cited_by_count": 0}
REAL = {"id": "https://openalex.org/A5019985343",
        "display_name": "Martin Steinegger",
        "orcid": "https://orcid.org/" + ORCID,
        "works_count": 175, "cited_by_count": 82591}


@pytest.fixture
def api(monkeypatch):
    """Record which author endpoints get asked, and answer them."""
    asked = []

    def fake_entity(path):
        asked.append(path)
        if path.startswith("orcid:"):
            return STUB
        if "A5019985343" in path:
            return REAL
        return None

    monkeypatch.setattr(metrics, "_fetch_author_entity", fake_entity)
    return asked


def test_the_stored_id_beats_the_orcid(api):
    rec = metrics._author_record("A5019985343", ORCID)

    assert rec["works_count"] == 175
    # And it did not waste a request finding that out.
    assert api == ["A5019985343"]


def test_orcid_alone_still_works(api):
    """Nothing changes for a trail row with no OpenAlex ID."""
    assert metrics._author_record(None, ORCID)["works_count"] == 2
    assert api == ["orcid:" + ORCID]


def test_a_dead_id_falls_back_to_the_orcid(api):
    rec = metrics._author_record("A-does-not-exist", ORCID)

    assert rec["works_count"] == 2
    assert api == ["A-does-not-exist", "orcid:" + ORCID]


def test_an_id_belonging_to_someone_else_is_refused(monkeypatch):
    """The guard against the fix becoming a new way to be wrong: if the
    stored ID resolves to a record carrying a *different* ORCID, it is
    not this person."""
    other = dict(REAL, orcid="https://orcid.org/0000-0002-1086-0253")

    def fake_entity(path):
        return STUB if path.startswith("orcid:") else other

    monkeypatch.setattr(metrics, "_fetch_author_entity", fake_entity)

    assert metrics._author_record("A5019985343", ORCID)["works_count"] == 2


def test_a_record_with_no_orcid_is_accepted(monkeypatch):
    """Plenty of real author records carry no ORCID at all; absence is
    not a mismatch."""
    no_orcid = dict(REAL)
    no_orcid.pop("orcid")
    monkeypatch.setattr(metrics, "_fetch_author_entity",
                        lambda path: no_orcid)

    assert metrics._author_record("A5019985343", ORCID)["works_count"] == 175


@pytest.mark.parametrize("value", [
    "https://orcid.org/0000-0001-8781-9753",
    "orcid.org/0000-0001-8781-9753",
    "0000-0001-8781-9753",
    " 0000-0001-8781-9753 ",
])
def test_orcid_forms_compare_equal(value):
    assert metrics._bare_orcid(value) == ORCID


def test_the_profile_uses_the_chosen_record(monkeypatch):
    monkeypatch.setattr(metrics, "_fetch_author_entity",
                        lambda path: STUB if path.startswith("orcid:")
                        else REAL)

    profile = metrics.fetch_author_profile(
        orcid=ORCID, openalex_id="A5019985343")

    assert profile["works_count"] == 175
    assert profile["cited_by_count"] == 82591
