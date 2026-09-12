"""Citations must render for every style, including page ranges.

Found 2026-09-12 while checking a claim that regenerating sidecars
fixed the citation output. It fixed the missing volume/issue/pages —
and thereby exposed a crash: `chicago-author-date` raised
`UnboundLocalError` on any paper with a page *range*.

The cause is a version mismatch in a vendored style. CSL 1.0.2
renamed `page-range-format="chicago"` to `chicago-15`/`chicago-16`;
citeproc-py only implements the old name, so `chicago-16` matches
none of its branches, the block that assigns `index` is skipped, and
`_format_last_page` raises on the next line.

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


def test_no_vendored_style_asks_for_a_page_format_citeproc_lacks():
    """The real lesson: a CSL file can name a feature the renderer
    does not implement, and the failure is a crash rather than a
    graceful fallback. citeproc-py knows `chicago`, `expanded`,
    `minimal` and `minimal-two`."""
    import re
    supported = {"chicago", "expanded", "minimal", "minimal-two"}
    for style in csl_format.list_styles():
        with open(style["path"]) as fh:
            for found in re.findall(r'page-range-format="([^"]+)"',
                                    fh.read()):
                assert found in supported, (style["key"], found)
