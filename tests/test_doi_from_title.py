"""Resolving a paper that prints no DOI anywhere.

Reported 2026-09-13: `substructure-solution-with-shelxd-gr2280.pdf`,
Schneider & Sheldrick, Acta Cryst D 2002, imported with `doi: null`,
no journal, and `authors: ["Acta Crystallographica Section D"]` — the
journal name lifted off the top of page 1. The PDF genuinely contains
no DOI: not in the text, not in the XMP, not in the filename. Every
scanner in the chain was working correctly and there was nothing to
find.

Three things had to be true before the title could rescue it:

  * **The obvious search returns the wrong paper.** OpenAlex's
    `title.search` for "Substructure solution with SHELXD" returns
    exactly one result, `10.3410/f.1009497.145157` — a Faculty
    Opinions *recommendation of* the paper, authored by the
    recommender. Importing that was worse than importing nothing.

  * **The real record is unfindable by title**, because OpenAlex
    stores it as "Substructure solution withSHELXD" — the journal
    italicised the program name and the space was lost on ingestion.
    Word-based matching cannot find it; letter-based matching can.

  * **The general `search` parameter does find it** (third of 889
    results), which is only safe with a strict acceptance test.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract, metrics

TITLE = "Substructure solution with SHELXD"
REAL = "10.1107/s0907444902011678"
F1000 = "10.3410/f.1009497.145157"


def works(*items):
    return {"results": [dict(doi="https://doi.org/" + d, title=t,
                             publication_year=y, authorships=[])
                        for d, t, y in items]}


def test_normalised_title_ignores_the_lost_space():
    assert (metrics.normalised_title("Substructure solution withSHELXD")
            == metrics.normalised_title(TITLE))
    assert (metrics.normalised_title("PHENIX: a comprehensive system")
            != metrics.normalised_title(TITLE))


def test_a_faculty_opinions_record_is_never_the_paper():
    assert metrics.is_recommendation_record(F1000, None) is True
    assert metrics.is_recommendation_record(
        "10.1234/x", "Faculty Opinions recommendation of " + TITLE) is True
    assert metrics.is_recommendation_record(REAL, TITLE) is False


def test_find_doi_skips_the_recommendation(monkeypatch):
    """The trap as it actually was: one result, and it is the wrong
    paper. Before this, `find_doi` returned it."""
    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: works((F1000,
                               "Faculty Opinions recommendation of " + TITLE,
                               2002)))

    assert metrics.find_doi(TITLE) is None


def test_title_search_accepts_the_mangled_title(monkeypatch):
    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: works(
            (F1000, "Faculty Opinions recommendation of " + TITLE, 2002),
            ("10.1107/s0108767307043930", "A short history of SHELX", 2007),
            (REAL, "Substructure solution withSHELXD", 2002)))

    assert metrics.find_doi_by_title_search(TITLE, year=2002) == REAL


def test_title_search_refuses_a_different_paper(monkeypatch):
    """889 results come back from a general search; only an exact
    normalised match may be accepted."""
    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: works(
            ("10.1107/s0108767307043930", "A short history of SHELX", 2007),
            ("10.1107/s0907444909052925", "PHENIX: a comprehensive system",
             2010)))

    assert metrics.find_doi_by_title_search(TITLE, year=2002) is None


def test_title_search_checks_the_year_loosely(monkeypatch):
    """A 2002 issue posted online in 2003 is still the same paper; a
    2015 one is not."""
    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: works((REAL, TITLE, 2003)))
    assert metrics.find_doi_by_title_search(TITLE, year=2002) == REAL

    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: works((REAL, TITLE, 2015)))
    assert metrics.find_doi_by_title_search(TITLE, year=2002) is None


def test_a_short_title_is_not_searched(monkeypatch):
    """"Methods" would match half the literature."""
    monkeypatch.setattr(
        metrics, "_http_get_json",
        lambda *a, **k: pytest.fail("should not have asked"))

    assert metrics.find_doi_by_title_search("Methods", year=2002) is None


def test_the_import_chain_tries_title_then_crossref(monkeypatch):
    """Order matters: the precise search first, the fuzzy one second,
    CrossRef's matcher last and only when surnames are known."""
    calls = []

    monkeypatch.setattr(metrics, "find_doi",
                        lambda *a, **k: calls.append("find_doi"))
    monkeypatch.setattr(metrics, "find_doi_by_title_search",
                        lambda *a, **k: calls.append("title_search"))
    monkeypatch.setattr(metrics, "find_doi_by_citation",
                        lambda *a, **k: calls.append("crossref") or REAL)

    got = extract._doi_by_title(
        {"title": TITLE, "year": 2002,
         "authors": ["Thomas R. Schneider", "George M. Sheldrick"]})

    assert got == REAL
    assert calls == ["find_doi", "title_search", "crossref"]


def test_the_import_chain_needs_a_title(monkeypatch):
    monkeypatch.setattr(metrics, "find_doi",
                        lambda *a, **k: pytest.fail("should not have asked"))

    assert extract._doi_by_title({"title": "", "year": 2002}) is None


def test_crossref_is_not_asked_without_surnames(monkeypatch):
    """Measured: CrossRef's matcher finds this paper from title plus
    surnames and not from title, journal and year together. Without
    names there is nothing to ask it."""
    monkeypatch.setattr(metrics, "find_doi", lambda *a, **k: None)
    monkeypatch.setattr(metrics, "find_doi_by_title_search",
                        lambda *a, **k: None)
    monkeypatch.setattr(metrics, "find_doi_by_citation",
                        lambda *a, **k: pytest.fail("should not have asked"))

    assert extract._doi_by_title({"title": TITLE, "year": 2002}) is None
