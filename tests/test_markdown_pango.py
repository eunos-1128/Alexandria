"""Summaries are written in Markdown; the card popover renders a
light subset of it as Pango markup.

Must never produce markup Pango rejects — a summary comes from a
model (or a person typing), so unbalanced or exotic input is
expected, and the fallback is plain escaped text rather than a
crash or a stray tag.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

markup = pytest.importorskip("alexandria.markup")
md = markup.markdown_to_pango


def _parses(s):
    return markup._markup_parses(s)


# ---- inline emphasis ----------------------------------------------

def test_bold():
    assert md("a **strong** claim") == "a <b>strong</b> claim"


def test_italic_with_asterisk_and_underscore():
    assert md("an *emphatic* point") == "an <i>emphatic</i> point"
    assert md("an _emphatic_ point") == "an <i>emphatic</i> point"


def test_inline_code():
    assert md("call `refmac5` here") == "call <tt>refmac5</tt> here"


def test_bold_wins_over_italic_for_double_markers():
    out = md("**both** and *one*")
    assert "<b>both</b>" in out and "<i>one</i>" in out


# ---- structure ----------------------------------------------------

def test_bullets_become_real_bullets():
    out = md("- first\n- second")
    assert "• first" in out and "• second" in out
    assert "- first" not in out


def test_asterisk_bullets_are_not_confused_with_italics():
    out = md("* first\n* second")
    assert "• first" in out and "• second" in out
    assert "<i>" not in out


def test_headings_become_bold_lines():
    out = md("## Methods\ntext")
    assert "<b>Methods</b>" in out
    assert "#" not in out


def test_paragraph_breaks_are_preserved():
    assert "\n\n" in md("one\n\ntwo")


# ---- safety --------------------------------------------------------

def test_escapes_markup_characters():
    out = md("5 < 6 & 7 > 2")
    assert "&lt;" in out and "&amp;" in out and "&gt;" in out
    assert _parses(out)


def test_angle_brackets_in_text_do_not_become_tags():
    out = md("the <script>alert</script> tag")
    assert "<script>" not in out
    assert _parses(out)


def test_unbalanced_markers_are_left_alone():
    for s in ("a ** dangling", "one * star", "back ` tick"):
        out = md(s)
        assert _parses(out), "unparseable for {!r}: {}".format(s, out)


def test_empty_and_none():
    assert md("") == ""
    assert md(None) == ""


def test_output_always_parses_for_awkward_input():
    awkward = [
        "**bold with `code` inside**",
        "_italic_ and **bold** and `tt` together",
        "* bullet with **bold**",
        "### heading with *emphasis*",
        "a ***triple*** marker",
        "100% & <tags> everywhere",
    ]
    for s in awkward:
        assert _parses(md(s)), "unparseable for {!r}".format(s)


# ---- bold-italic, and how a failure is allowed to degrade --------
#
# Reported 2026-09-14 from a real summary: the card showed raw
# Markdown for forty lines because one species name was written
# ***Campylobacter concisus***. The bold rule took two of the three
# stars and the italic rule closed across the bold span, giving
# `<b><i>x</b></i>` — interleaved, which Pango rejects — and the
# whole-string fallback then flattened everything.
#
# Note the awkward-input test above already covered "a ***triple***
# marker" and passed throughout: it asserted only that the output
# parses, which the degraded path satisfies. Not crashing is not the
# same as working.

def test_bold_italic_nests_properly():
    out = md("***Campylobacter concisus***")

    assert out == "<b><i>Campylobacter concisus</i></b>"
    assert _parses(out)


def test_bold_italic_inside_a_sentence():
    out = md("down to one organism: ***C. concisus***, a commensal.")

    assert "<b><i>C. concisus</i></b>" in out
    assert "*" not in out


def test_one_bad_line_does_not_flatten_the_rest():
    """The part that made a small fault look like a total one."""
    text = "\n".join([
        "# Overview",
        "A **bold** claim.",
        "an **unclosed span that Pango will reject <",
        "Another *emphasised* line.",
    ])

    out = md(text).split("\n")

    assert out[0] == "<b>Overview</b>"
    assert "<b>bold</b>" in out[1]
    assert "<i>emphasised</i>" in out[3], "a later line still renders"


def test_a_rejected_line_keeps_its_own_text_intact():
    out = md("**a *tangled** span*")

    assert _parses(out)
    # Escaped, not silently dropped: the reader still sees the words.
    assert "tangled" in out


def test_a_whole_real_summary_renders():
    """The shape the reported one had: headings, bullets, bold, a
    bold-italic species name, and quotes."""
    text = "\n".join([
        "# Overview",
        "",
        "They narrow it to ***Campylobacter concisus***, an oral "
        "commensal.",
        "",
        "# The two consortia",
        "",
        '- **cSI-I** ("inflammation-inducing"): all **184 strains**.',
        "- **cSI-N**: a **39-isolate** subset.",
    ])

    out = md(text)

    assert _parses(out)
    assert "<b>Overview</b>" in out
    assert "<b><i>Campylobacter concisus</i></b>" in out
    assert "• <b>cSI-I</b>" in out
    assert "**" not in out, "no literal markers left for the reader"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
