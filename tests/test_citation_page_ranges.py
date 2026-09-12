"""Citations must render for every style, including page ranges.

Found 2026-09-12 while checking a claim that regenerating sidecars
fixed the citation output. It fixed the missing volume/issue/pages —
and thereby exposed a crash: `chicago-author-date` raised
`UnboundLocalError` on any paper with a page *range*.

The cause is a version mismatch in a vendored style. CSL 1.0.2
renamed `page-range-format="chicago"` to `chicago-15`/`chicago-16`;
citeproc-py 0.9.2 only implements the old name, so `chicago-16`
matched none of its branches, the block that assigns `index` was
skipped, and `_format_last_page` raised on the next line. Fixed
upstream; the pin now requires >=0.11.1.

Latent until the volume/issue/pages work landed, because before that
no sidecar carried a `pages` field at all. Nineteen did by the time
this was found.
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import csl_format

STYLES = [s["key"] for s in csl_format.list_styles()]

RECORD = {
    "title": "Metabolic Engineering of Yeast",
    "authors": ["Shuobo Shi", "Yu Chen", "Jens Nielsen"],
    "year": 2025,
    "journal": "Annual Review of Biophysics",
    "doi": "10.1146/annurev-biophys-070924-103134",
    "volume": "54", "issue": "1", "pages": "101-120",
}


@pytest.mark.parametrize("style", STYLES)
@pytest.mark.parametrize("pages", ["101-120", "101–120", "101-20",
                                   "9-11", "1001-1010", "101", ""])
def test_every_style_renders_every_page_range(style, pages):
    """The crash was specific to ranges: a single page was fine, so a
    library with no page data looked healthy."""
    rec = dict(RECORD, pages=pages)

    out = csl_format.format_citation(rec, style)

    assert out and RECORD["title"] in out


@pytest.mark.parametrize("style", STYLES)
def test_the_page_range_reaches_the_output(style):
    out = csl_format.format_citation(RECORD, style)

    assert "101" in out


def test_the_renderer_is_new_enough_for_the_vendored_styles():
    """The real lesson: a CSL file can name a feature the renderer
    does not implement, and the failure is a crash rather than a
    graceful fallback.

    citeproc-py 0.9.2 knew only `page-range-format="chicago"`, while
    CSL 1.0.2 renamed it `chicago-15`/`chicago-16` — so the canonical
    Chicago style crashed. Rather than editing the vendored style to
    suit an old renderer, the pin requires one that implements it.
    This test fails loudly if that pin is ever relaxed."""
    import citeproc

    major, minor = (int(p) for p in citeproc.__version__.split(".")[:2])

    assert (major, minor) >= (0, 11), citeproc.__version__


# --- the space second-field-align drops in plain text ----------------

@pytest.mark.parametrize("style,opener", [
    ("vancouver", "[1] "),
    ("nature", "1. "),
])
def test_a_numbered_entry_has_a_space_after_its_number(style, opener):
    """Vancouver and Nature set second-field-align="flush", which on
    a rendered page means the number sits in its own column. Plain
    text has no columns, so citeproc emits "[1]Shi" and "1.Shi"."""
    out = csl_format.format_citation(RECORD, style)

    assert out.startswith(opener), out[:40]


@pytest.mark.parametrize("style", ["apa", "chicago-author-date"])
def test_styles_that_open_with_a_name_are_untouched(style):
    out = csl_format.format_citation(RECORD, style)

    assert out.startswith("Shi"), out[:40]


def test_an_in_text_marker_is_left_alone():
    """"[1]" on its own is complete; the fix must not append a space
    to it. The pattern requires a non-space to follow."""
    assert csl_format.format_citation(
        RECORD, "vancouver", mode="citation") == "[1]"


@pytest.mark.parametrize("text,expected", [
    ("[1]Shi S", "[1] Shi S"),
    ("1.Shi, S.", "1. Shi, S."),
    ("[12]Abc", "[12] Abc"),
    ("[1] Shi S", "[1] Shi S"),      # already spaced, unchanged
    ("1. Shi", "1. Shi"),
    ("[1]", "[1]"),                  # nothing follows
    ("Shi, S. (2025).", "Shi, S. (2025)."),
    ("", ""),
])
def test_the_rule_only_fires_where_the_output_is_wrong(text, expected):
    assert csl_format._space_after_flush_number(text) == expected
