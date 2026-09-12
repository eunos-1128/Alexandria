"""Publishers misprint their own DOI.

Reported 2026-09-08: adding reference 41 fetched the right PDF and
then threw it away —

    PDF rejected: its DOI 10.1146/annurev-biophys-052118115647
    doesn't match the BibTeX entry's 10.1146/annurev-biophys-052118-115647

Annual Reviews typesets that paper's front matter with the hyphen
missing from the text stream, so scraping the file yields a DOI that
does not resolve, while the BibTeX entry — which came from CrossRef
— is correct. Comparing the two verbatim rejects a perfectly good
PDF and leaves the user with a ghost card.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import bibtex_import

REAL = "10.1146/annurev-biophys-052118-115647"
AS_PRINTED = "10.1146/annurev-biophys-052118115647"


def test_the_reported_pair_is_one_doi():
    assert bibtex_import._hyphen_variant(REAL, AS_PRINTED)


def test_different_papers_are_still_different():
    assert not bibtex_import._hyphen_variant(
        REAL, "10.1038/s41586-021-03819-2")
    assert not bibtex_import._hyphen_variant(
        "10.1107/S2059798325001251", "10.1107/S2059798325001252")


def test_a_missing_doi_is_never_a_variant():
    """Absent is not "equal after normalisation" — the check has its
    own path for a PDF or ghost with no DOI at all."""
    assert not bibtex_import._hyphen_variant("", "")
    assert not bibtex_import._hyphen_variant(REAL, "")
    assert not bibtex_import._hyphen_variant(None, REAL)


def test_only_hyphens_are_treated_loosely():
    """Not a general punctuation-insensitive compare: a DOI differing
    by a dot or a slash is a different DOI."""
    assert not bibtex_import._hyphen_variant(
        "10.1146/annurev.biophys.052118.115647", REAL)


def test_the_gate_accepts_a_hyphen_variant(monkeypatch):
    monkeypatch.setattr(bibtex_import.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: AS_PRINTED)

    ok, msg = bibtex_import._ghost_doi_check(REAL, "/x/paper.pdf")

    assert ok and msg == ""


def test_the_gate_still_rejects_the_wrong_paper(monkeypatch):
    """The check exists because a fetched PDF can genuinely be the
    wrong article; loosening hyphens must not cost that."""
    monkeypatch.setattr(bibtex_import.extract, "_scan_doi_in_pages",
                        lambda p, max_pages=4: "10.1038/s41586-021-03819-2")

    ok, msg = bibtex_import._ghost_doi_check(REAL, "/x/paper.pdf")

    assert not ok
    assert "doesn't match" in msg
