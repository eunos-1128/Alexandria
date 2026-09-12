"""bioRxiv: the new DOI prefix, and fetching the PDF anyway.

Two findings from 2026-09-09, re-measured 2026-09-12.

**The prefix changed.** bioRxiv moved from 10.1101 to 10.64898 in
late 2025, and the filename rule only knew the old one — so a
download named `2026.09.09.750088.full.pdf` was confidently labelled
`10.1101/2026.09.09.750088`, a DOI belonging to nobody. Worse than
missing, because a wrong DOI stops every later step looking
elsewhere.

The changeover was not clean. Measured against Europe PMC, ids
posted between 2025.11.20 and 2025.12.01 were issued under *both*
prefixes — 111 papers under the old and 889 under the new in that
fortnight. Inside that window the filename cannot tell you which, so
the rule declines rather than guesses.

**And the PDFs are fetchable although every flag says otherwise.**
Europe PMC reports `hasPDF: "N"` and `isOpenAccess: "N"` for these
papers and offers no usable link, yet resolving the DOI and
appending `.full.pdf` returns the paper. Those flags describe what
Europe PMC holds, not what is reachable.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract, pdf_fetch


# --- which prefix a filename implies ---------------------------------

@pytest.mark.parametrize("name,expected", [
    ("2024.05.24.595765.full.pdf", "10.1101/2024.05.24.595765"),
    ("2025.11.19.688000.full.pdf", "10.1101/2025.11.19.688000"),
    ("2025.12.02.692000.full.pdf", "10.64898/2025.12.02.692000"),
    ("2026.09.09.750088.full.pdf", "10.64898/2026.09.09.750088"),
])
def test_the_prefix_follows_the_date_in_the_id(name, expected):
    assert extract._doi_from_filename("/lib/" + name) == expected


@pytest.mark.parametrize("ident", [
    "2025.11.20.689376", "2025.11.25.690000", "2025.12.01.691628",
])
def test_the_changeover_window_declines_to_guess(ident):
    """Both prefixes were in use for these dates. A confidently wrong
    DOI is worse than none — the text scan picks these up instead."""
    assert extract._biorxiv_doi_for_id(ident) is None


def test_a_real_paper_is_not_labelled_with_the_old_prefix():
    """The reported case: this file's DOI is 10.64898/…, and the rule
    used to answer 10.1101/… ."""
    got = extract._doi_from_filename("/lib/2026.09.09.750088.full.pdf")

    assert got == "10.64898/2026.09.09.750088"
    assert not got.startswith("10.1101")


# --- recognising a preprint DOI --------------------------------------

@pytest.mark.parametrize("doi", [
    "10.1101/2024.05.24.595765", "10.64898/2026.09.09.750088",
    "10.64898/2025.12.02.692000",
])
def test_both_prefixes_are_recognised_as_preprints(doi):
    assert pdf_fetch.looks_like_preprint_doi(doi)


@pytest.mark.parametrize("doi", [
    "10.1038/s41586-021-03819-2", "10.1146/annurev-biophys-070924-103134",
    "", None,
])
def test_other_dois_are_not(doi):
    assert not pdf_fetch.looks_like_preprint_doi(doi)


# --- the fetch strategy ----------------------------------------------

def test_the_landing_page_becomes_a_pdf_url(monkeypatch):
    """Resolve, then append — the version suffix is part of the path
    and only the redirect knows which version is current."""
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://www.biorxiv.org/content/10.64898/2026.09.09.750088v2"

    monkeypatch.setattr(pdf_fetch.urllib.request, "urlopen",
                        lambda *a, **k: FakeResponse())

    urls = pdf_fetch._biorxiv_pdf_urls("10.64898/2026.09.09.750088")

    assert urls == ["https://www.biorxiv.org/content/"
                    "10.64898/2026.09.09.750088v2.full.pdf"]


def test_a_non_preprint_doi_is_not_resolved(monkeypatch):
    """One request per fetch is worth avoiding when it cannot help."""
    monkeypatch.setattr(
        pdf_fetch.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("should not have been asked"))

    assert pdf_fetch._biorxiv_pdf_urls("10.1038/s41586-021-03819-2") == []


def test_a_redirect_somewhere_else_is_refused(monkeypatch):
    """A DOI that lands off the preprint servers is not ours to
    guess a PDF path for."""
    class Elsewhere:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def geturl(self):
            return "https://www.nature.com/articles/s41586-021-03819-2"

    monkeypatch.setattr(pdf_fetch.urllib.request, "urlopen",
                        lambda *a, **k: Elsewhere())

    assert pdf_fetch._biorxiv_pdf_urls("10.1101/2024.05.24.595765") == []
