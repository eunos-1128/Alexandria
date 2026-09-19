"""A citation listed twice at the same spot must resolve to the numbered
copy.

Found 2026-09-19 while testing in-place references against a real
paper. `build_citation_links` runs its recovery paths (C–E) exactly
when the publisher's link annotations (Path A) carry no reference
number, and it *appends* what they find. So a paper could end up with
every citation listed twice: the publisher's annotation first — no
number, pointing only at the bibliography's page — and the recovered
link after it, numbered and positioned, the two rects within 0.3 pt.

`_citation_at` returns the first match, so every click hit the useless
copy: a jump to the top of the bibliography page and no popover. That
predates the in-place popover; it only became visible because testing
that change needed a click that really landed on a numbered link.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from alexandria import viewer

# The real rects from page 1 of the paper that showed it.
PUB_1 = ((323.9, 256.1, 328.3, 265.1), 8, None, None)
REC_1 = ((323.9, 256.0, 328.3, 264.8), 8, 538.0, 1)
PUB_2 = ((333.1, 256.1, 337.4, 265.1), 8, None, None)
REC_2 = ((333.1, 256.0, 337.4, 264.8), 8, 501.4, 2)


def test_the_publisher_copy_under_a_numbered_link_goes():
    out = viewer._drop_shadowed_links({0: [PUB_1, PUB_2, REC_1, REC_2]})

    assert out == {0: [REC_1, REC_2]}


def test_an_unnumbered_link_with_nothing_on_top_survives():
    """For a citation the recovery missed, the publisher's annotation
    is the only link there is — dropping it would make the citation
    unclickable."""
    lonely = ((100.0, 100.0, 105.0, 109.0), 8, None, None)

    out = viewer._drop_shadowed_links({0: [PUB_1, REC_1, lonely]})

    assert lonely in out[0]
    assert PUB_1 not in out[0]


def test_a_page_with_no_numbered_links_is_untouched():
    """The publisher-only case, which worked before and must not
    change."""
    links = {3: [PUB_1, PUB_2]}

    assert viewer._drop_shadowed_links(links) == links


def test_numbered_links_are_never_dropped():
    """Even two numbered links at one spot — "(6, 7)" can be split into
    adjacent rects that touch."""
    six = ((10.0, 10.0, 20.0, 20.0), 8, 400.0, 6)
    seven = ((10.0, 10.0, 20.0, 20.0), 8, 380.0, 7)

    out = viewer._drop_shadowed_links({0: [six, seven]})

    assert out == {0: [six, seven]}


def test_coverage_needs_most_of_the_rect():
    small = (0.0, 0.0, 10.0, 10.0)

    assert viewer._mostly_covered(small, (0.0, 0.0, 10.0, 10.0))
    assert viewer._mostly_covered(small, (0.0, 0.0, 6.0, 10.0))     # 60 %
    assert not viewer._mostly_covered(small, (0.0, 0.0, 4.0, 10.0))  # 40 %
    assert not viewer._mostly_covered(small, (20.0, 20.0, 30.0, 30.0))


def test_coverage_ignores_corner_order():
    """PDF rects arrive with either corner first."""
    assert viewer._mostly_covered((10.0, 10.0, 0.0, 0.0),
                                  (0.0, 10.0, 10.0, 0.0))


def test_a_click_now_lands_on_the_numbered_link():
    """End to end through the real hit-test."""
    import types

    class _V:
        _citation_at = viewer.PdfViewerWindow._citation_at

        def __init__(self, links):
            self.citation_links = links
            self.doc = types.SimpleNamespace(
                get_page=lambda i: types.SimpleNamespace(
                    get_size=lambda: (612.0, 792.0)))

    raw = {0: [PUB_1, PUB_2, REC_1, REC_2]}
    x, y_down = 326.0, 792.0 - 260.0          # the centre of "[1]"

    assert _V(raw)._citation_at(0, x, y_down)[3] is None      # the bug
    fixed = _V(viewer._drop_shadowed_links(raw))
    assert fixed._citation_at(0, x, y_down)[3] == 1
