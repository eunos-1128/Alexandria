"""Find-as-you-type that marks matches instead of hiding rows.

Asked for 2026-09-16. An author's works list is up to 50 rows in a
meaningful order — newest first, or most cited first — and that order
is the information: "where in the career do the cryo-EM papers fall,
and are they the well-cited ones?" Filtering answers "which" and
destroys "where", so this highlights and leaves every row in place.

The awkward part is that titles are not plain text. OpenAlex and JATS
send real inline markup (`<i>`, `<sub>`), which `safe_pango_markup`
deliberately keeps, so the marking has to survive tags without cutting
one in half and without disturbing the spacing around it.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import find_text as ft

MARK = '<span background="#fff200" foreground="#000000">'


def test_a_match_is_marked():
    out = ft.highlight("Cryo-EM model building at speed", "cryo")

    assert out == MARK + "Cryo</span>-EM model building at speed"


def test_matching_ignores_case_and_marks_every_hit():
    out = ft.highlight("Cryo-EM and cryo-ET", "cryo")

    assert out.count(MARK) == 2
    assert ">Cryo</span>" in out and ">cryo</span>" in out


def test_no_match_is_the_plain_escaped_text():
    assert ft.highlight("Ribosome structure", "zzz") == "Ribosome structure"
    assert ft.highlight("R&D notes", "zzz") == "R&amp;D notes"


def test_one_character_is_not_a_search():
    """It would mark most of the list and the count would say nothing
    worth reading."""
    assert ft.highlight("Cryo-EM", "c") == "Cryo-EM"
    assert ft.spans("Cryo-EM", "c") == []


def test_the_spacing_around_preserved_markup_is_not_disturbed():
    """The fault that ruled out escaping fragment by fragment: the
    space before "cryo" went missing."""
    out = ft.highlight("H<sub>2</sub>O in cryo-EM maps", "cryo")

    assert " " + MARK + "cryo</span>-EM maps" in out
    assert "<sub>2</sub>" in out


def test_a_match_inside_preserved_markup_still_marks():
    out = ft.highlight("Structure of <i>E. coli</i> ribosome", "coli")

    assert "<i>E. " + MARK + "coli</span></i>" in out


def test_a_query_that_would_cut_a_tag_marks_nothing():
    """Better an unmarked match than markup Pango rejects."""
    out = ft.highlight("a <i>cryo</i> b", "cryo</i")

    assert out == "a <i>cryo</i> b"


def test_a_query_with_an_ampersand_still_matches():
    """The text is escaped, so the needle has to be escaped the same
    way or "R&D" could never be found."""
    out = ft.highlight("Smith & Jones, R&D methods", "r&d")

    assert MARK + "R&amp;D</span>" in out


def test_rows_are_chosen_by_any_visible_field():
    rows = [["Cryo-EM of the ribosome", "A Smith", "2024 · Nature"],
            ["Ribosome assembly", "B Cryo", "2023 · Cell"],
            ["Unrelated", "C Jones", "2022 · Science"]]

    assert ft.matching_indices(rows, "cryo") == [0, 1]


def test_the_summary_counts_rows_not_hits():
    assert ft.summary(7, 50, "cryo") == "7 of 50"
    assert ft.summary(0, 50, "zzz") == "no matches"


def test_the_summary_says_nothing_before_there_is_a_search():
    """A count that appears while the box is empty reads as a
    result."""
    assert ft.summary(0, 50, "") == ""
    assert ft.summary(0, 50, "c") == ""


@pytest.mark.parametrize("text", [None, ""])
def test_empty_text_is_harmless(text):
    assert ft.highlight(text, "cryo") == ""
    assert ft.spans(text, "cryo") == []
