"""A DOI that wraps across a line break at one of its own hyphens.

Reported 2026-09-08: adding a reference fetched the right PDF and
rejected it, because the DOI scraped from the file
(`10.1146/annurev-biophys-070924103134`) did not match the BibTeX
entry's (`…-070924-103134`).

The hyphen is in the file. Annual Reviews sets the DOI in a narrow
front-matter column, so it wraps — and `pdftotext` in its *default*
mode joins such a line and drops the trailing hyphen, taking it for
word hyphenation. `_scrape_doi` has always known how to stitch
`…-\\n…` back together; the hyphen simply never survived long enough
for it to see.

`-layout` is not the answer either: it preserves the hyphen but sets
the neighbouring column on the same visual line, so the stitch glues
the DOI to whatever word sits beside it (`…-021424-Since`). `-raw`
emits content-stream order, where the continuation of a wrapped DOI
is the next thing along.
"""

import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract

DOI = "10.1146/annurev-biophys-070924-103134"


def _pdf_with_wrapped_doi(path, head, tail):
    """A PDF whose DOI is split across two lines, as a narrow column
    forces it to be."""
    import cairo
    surf = cairo.PDFSurface(path, 595, 842)
    cr = cairo.Context(surf)
    cr.select_font_face("sans")
    cr.set_font_size(9)
    cr.move_to(50, 100)
    cr.show_text("The Annual Review of Biophysics is online at")
    cr.move_to(50, 112)
    cr.show_text(head)
    cr.move_to(50, 124)
    cr.show_text(tail)
    cr.show_page()
    surf.finish()


@pytest.mark.skipif(not extract.shutil.which("pdftotext"),
                    reason="pdftotext not installed")
def test_a_doi_wrapped_at_a_hyphen_is_recovered_whole(tmp_path):
    pdf = str(tmp_path / "wrapped.pdf")
    _pdf_with_wrapped_doi(
        pdf, "https://doi.org/10.1146/annurev-biophys-070924-", "103134")

    assert extract._scan_doi_in_pages(pdf, max_pages=2) == DOI


@pytest.mark.skipif(not extract.shutil.which("pdftotext"),
                    reason="pdftotext not installed")
def test_an_unwrapped_doi_is_unaffected(tmp_path):
    pdf = str(tmp_path / "plain.pdf")
    _pdf_with_wrapped_doi(pdf, "https://doi.org/" + DOI, "Copyright 2025")

    assert extract._scan_doi_in_pages(pdf, max_pages=2) == DOI


def test_the_stitch_keeps_the_hyphen():
    """The rule `_scrape_doi` applies once the hyphen reaches it."""
    text = "https://doi.org/10.1146/annurev-biophys-070924-\n103134\n"

    assert extract._scrape_doi(text) == DOI


def test_a_hyphenless_join_is_not_silently_accepted():
    """What the default pdftotext mode produced. Nothing can recover
    it after the fact — which is why the fix has to be upstream, in
    how the text is extracted."""
    assert extract._scrape_doi(
        "https://doi.org/10.1146/annurev-biophys-070924103134") == \
        "10.1146/annurev-biophys-070924103134"


def test_scan_uses_raw_mode(tmp_path, monkeypatch):
    """Guard the choice itself: default mode drops the hyphen and
    -layout splices in the neighbouring column."""
    seen = {}
    real = subprocess.run

    def spy(cmd, *a, **k):
        if cmd and cmd[0] == "pdftotext":
            seen["cmd"] = cmd
        return real(cmd, *a, **k)

    monkeypatch.setattr(extract.subprocess, "run", spy)
    pdf = str(tmp_path / "x.pdf")
    _pdf_with_wrapped_doi(pdf, "https://doi.org/" + DOI, "x")
    extract._scan_doi_in_pages(pdf, max_pages=1)

    assert "-raw" in seen["cmd"]
    assert "-layout" not in seen["cmd"]
