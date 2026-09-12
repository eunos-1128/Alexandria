"""Funders aggregated across the papers you hold.

Asked for 2026-09-10: "for the PIs, are we in a position to ask where
the funding for this research comes from?" Partly. OpenAlex already
attaches `funders` and `grants` to each work and the importer stores
both — 117 of 199 papers in the reference library carry funders. What
that supports is "which funders appear on the papers I have by this
person", which is not the same question as "which grants does this PI
hold": funder attribution belongs to the paper, so every co-author of
a consortium paper inherits all of its grants.

The award numbers are the weak part. Measured across the library, 13
of the 85 papers with two or more awards attach at least one to the
wrong funder — CrossRef deposits funders and awards as parallel
arrays and they arrive mis-zipped.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import funding

ADA = {"name": "Ada Lovelace", "openalex_id": "A1", "orcid": "0000-1"}
BOB = {"name": "Bob Stone", "openalex_id": "A2"}


def paper(title, year, authors, funders=None, grants=None):
    return {"title": title, "year": year, "authorships": authors,
            "funders": funders or [], "grants": grants or []}


LIBRARY = [
    paper("One", 2015, [ADA, BOB], ["Wellcome Trust"],
          [{"funder": "Wellcome Trust", "award_id": "086185/Z/08/Z"}]),
    paper("Two", 2020, [ADA], ["Wellcome Trust", "Medical Research Council"],
          [{"funder": "Medical Research Council", "award_id": "MR/J002976/1"}]),
    paper("Three", 2023, [BOB], ["Royal Society"], []),
]


def test_a_funder_is_counted_once_per_paper():
    rows = funding.profile(LIBRARY, openalex_id="A1")
    wellcome = [r for r in rows if r["funder"] == "Wellcome Trust"][0]
    assert wellcome["n_papers"] == 2


def test_only_this_authors_papers_count():
    rows = funding.profile(LIBRARY, openalex_id="A2")
    assert {r["funder"] for r in rows} == {"Wellcome Trust", "Royal Society"}


def test_the_span_of_years_is_reported():
    rows = funding.profile(LIBRARY, openalex_id="A1")
    wellcome = [r for r in rows if r["funder"] == "Wellcome Trust"][0]
    assert (wellcome["first_year"], wellcome["last_year"]) == (2015, 2020)
    assert funding.summarise(wellcome) == "2015–2020 · 2 papers"


def test_awards_are_kept_but_not_in_the_summary_line():
    """They are the least reliable field, so they belong on hover."""
    rows = funding.profile(LIBRARY, openalex_id="A1")
    mrc = [r for r in rows if r["funder"].startswith("Medical")][0]
    assert mrc["awards"] == ["MR/J002976/1"]
    assert "MR/" not in funding.summarise(mrc)


def test_ranked_by_papers_then_alphabetically():
    """Stable order, so two funders on one paper each do not swap
    places between openings of the same page."""
    rows = funding.profile(LIBRARY, openalex_id="A1")
    assert [r["funder"] for r in rows] == [
        "Wellcome Trust", "Medical Research Council"]


def test_spellings_of_one_funder_are_merged():
    """Real case from the library: the same Italian ministry appears
    with a curly apostrophe and a straight one."""
    a = "Ministero dell’Istruzione, dell’Università e della Ricerca"
    b = "Ministero dell'Istruzione, dell'Universita e della Ricerca"
    papers = [paper("A", 2022, [ADA], [a]), paper("B", 2023, [ADA], [b])]

    rows = funding.profile(papers, openalex_id="A1")

    assert len(rows) == 1
    assert rows[0]["n_papers"] == 2


def test_a_the_prefix_does_not_split_a_funder():
    papers = [paper("A", 2022, [ADA], ["The Wellcome Trust"]),
              paper("B", 2023, [ADA], ["Wellcome Trust"])]

    assert len(funding.profile(papers, openalex_id="A1")) == 1


def test_a_funder_named_only_in_a_grant_still_counts():
    """`funders` and `grants` disagree in real records."""
    papers = [paper("A", 2022, [ADA], [],
                    [{"funder": "Diamond Light Source", "award_id": "1"}])]

    rows = funding.profile(papers, openalex_id="A1")

    assert rows[0]["funder"] == "Diamond Light Source"


def test_identity_beats_name():
    """Two people share a name more often than an OpenAlex ID."""
    other_ada = {"name": "Ada Lovelace", "openalex_id": "A99"}
    papers = [paper("A", 2022, [other_ada], ["Wellcome Trust"])]

    assert funding.profile(papers, openalex_id="A1") == []
    assert funding.profile(papers, name="Ada Lovelace") != []


def test_an_author_with_no_funded_papers_gets_nothing():
    assert funding.profile(LIBRARY, openalex_id="A404") == []


def test_junk_records_do_not_raise():
    papers = [{"title": "x", "year": None, "authorships": "not json",
               "funders": None, "grants": [None, {"funder": None}]},
              {"title": "y", "authorships_json": "[]"}]

    assert funding.profile(papers, name="Ada Lovelace") == []
