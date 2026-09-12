"""When the "REFERENCES" heading and the first entry share a line.

Reported 2026-09-08 against PIIS0969212624003319 (Structure, 2024):
citation clicks jumped to the right place in the reference list but
no reference popover appeared. The cause was upstream of the popover
— `parse_bibliography` returned nothing, so no path could assign a
reference number to any link, and the popover only fires when it has
one.

Poppler's text layout returned the heading run together with the
first entry — `"REFERENCES14. Cawez, F., …"` — because Elsevier's
two-column layout puts them in the same y-band. `_HEADER_RE` requires
the heading to be a line of its own, so the bibliography was never
located at all.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import references_pdf as R


@pytest.mark.parametrize("line", [
    "REFERENCES14. Cawez, F., Duray, E., Hu, Y., Vandenameele, J.",
    "References1. Lubin, J.H., Zardecki, C.",
    "BIBLIOGRAPHY23) Smith, A.",
    "Literature Cited7. Jones, B.",
])
def test_a_heading_glued_to_its_first_entry_is_recognised(line):
    assert R._HEADER_GLUED_RE.match(line) is not None


@pytest.mark.parametrize("line", [
    "references therein 12 show that",     # prose
    "REFERENCES",                          # the ordinary case
    "see references 4 and 5",
    "Reference Manager output",
    "referenced works are listed below",
])
def test_prose_mentioning_references_is_not_a_heading(line):
    """The marker requirement is what keeps this from firing on
    ordinary sentences."""
    assert R._HEADER_GLUED_RE.match(line) is None


def test_the_glued_entry_is_kept_not_discarded():
    """The heading is stripped and the entry underneath survives —
    otherwise the bibliography starts one entry short and the marker
    walk never sees its number."""
    line = "REFERENCES14. Cawez, F., Duray, E."
    m = R._HEADER_GLUED_RE.match(line)

    assert line[m.end():] == "14. Cawez, F., Duray, E."


def test_a_standalone_heading_still_matches_the_original_rule():
    """The ordinary layout must keep working: the glued rule is an
    addition, not a replacement."""
    assert R._HEADER_RE.match("References") is not None
    assert R._HEADER_RE.match("5. References") is not None
    assert R._HEADER_GLUED_RE.match("References") is None
