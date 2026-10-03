"""Summaries are written in Markdown; the card popover renders them
into a TextView via summary_view, styled from summary_style.json.

These check the buffer the renderer produces — the text a reader sees
and which tags cover it — not pixels.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

pytest.importorskip("markdown_it")

try:
    import gi
    gi.require_version("Gtk", "4.0")
    from gi.repository import Gtk
    _display_ok = bool(Gtk.init_check())
except Exception:
    _display_ok = False

pytestmark = pytest.mark.skipif(
    not _display_ok, reason="no display for GTK tests")

from alexandria import summary_view  # noqa: E402


def _render(text, dark=False):
    buf = Gtk.TextBuffer()
    links = summary_view.render_markdown(buf, text, dark)
    return buf, links


def _text(buf):
    return buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)


def _tags_at(buf, needle):
    """Names of the tags covering the first character of `needle`."""
    start = _text(buf).index(needle)
    return {t.props.name for t in buf.get_iter_at_offset(start).get_tags()}


# ---- the style file ------------------------------------------------

def test_style_file_is_valid_json_with_light_and_dark_colours():
    with open(summary_view._STYLE_PATH, encoding="utf-8") as f:
        style = json.load(f)
    for name, spec in style["tags"].items():
        for key, value in spec.items():
            if isinstance(value, dict):
                assert set(value) == {"light", "dark"}, (name, key)


def test_theme_picks_the_matching_colour():
    light, _ = _render("# Title")
    dark, _ = _render("# Title", dark=True)
    fg = lambda b: b.get_tag_table().lookup("h1").props.foreground_rgba  # noqa: E731
    assert fg(light).to_string() != fg(dark).to_string()


def test_null_leaves_property_unset_in_that_theme():
    light, _ = _render("```\nx = 1\n```")
    dark, _ = _render("```\nx = 1\n```", dark=True)
    tag = lambda b: b.get_tag_table().lookup("code-block")  # noqa: E731
    assert not tag(light).props.foreground_set
    assert tag(dark).props.foreground_set


# ---- text and structure -------------------------------------------

def test_soft_break_is_a_space():
    buf, _ = _render("This is\nsome text.")
    assert _text(buf) == "This is some text."


def test_hard_break_is_a_newline():
    buf, _ = _render("one  \ntwo")
    assert _text(buf) == "one\ntwo"


def test_emphasis_spanning_a_line_break():
    buf, _ = _render("a **bold\nphrase** here")
    assert "bold" in _tags_at(buf, "phrase")


def test_inline_styles():
    buf, _ = _render("**b** *i* `c` ~~s~~")
    assert "bold" in _tags_at(buf, "b")
    assert "italic" in _tags_at(buf, "i")
    assert "inline-code" in _tags_at(buf, "c")
    assert "strikethrough" in _tags_at(buf, "s")


def test_heading_tag_covers_inline_children():
    buf, _ = _render("## The `refmac5` step")
    assert "h2" in _tags_at(buf, "The")
    assert {"h2", "inline-code"} <= _tags_at(buf, "refmac5")


def test_blocks_are_separate_lines():
    buf, _ = _render("# H\n\nPara one.\n\nPara two.")
    assert _text(buf) == "H\nPara one.\nPara two."
    assert "paragraph" in _tags_at(buf, "Para one")


def test_bullets_and_nesting():
    buf, _ = _render("- first\n  - inner\n- second")
    assert _text(buf) == "• first\n◦ inner\n• second"
    assert "list-item-1" in _tags_at(buf, "first")
    assert "list-item-2" in _tags_at(buf, "inner")


def test_nested_list_indents_one_step_further():
    buf, _ = _render("- a\n  - b")
    table = buf.get_tag_table()
    step = summary_view._style()["list_indent_step"]
    assert (table.lookup("list-item-2").props.left_margin
            - table.lookup("list-item-1").props.left_margin) == step


def test_ordered_list_respects_start():
    buf, _ = _render("3. three\n4. four")
    assert _text(buf) == "3. three\n4. four"


def test_asterisk_bullets_are_not_italics():
    buf, _ = _render("* first\n* second")
    assert "• first" in _text(buf)
    assert "italic" not in _tags_at(buf, "first")


def test_blockquote():
    buf, _ = _render("> quoted")
    assert "blockquote" in _tags_at(buf, "quoted")


def test_code_block_keeps_lines():
    buf, _ = _render("```python\na = 1\nb = 2\n```")
    assert _text(buf) == "a = 1\nb = 2"
    assert "code-block" in _tags_at(buf, "b = 2")


def test_table_is_aligned_text():
    buf, _ = _render("| a | bb |\n|---|---|\n| 1 | 2 |")
    lines = _text(buf).split("\n")
    assert lines[0] == "| a   | bb  |"
    assert lines[1] == "|-----+-----|"
    assert lines[2] == "| 1   | 2   |"
    assert "table-header" in _tags_at(buf, "a ")


def test_links_are_tagged_and_recorded():
    buf, links = _render("see [the paper](https://doi.org/10.1/x)")
    names = _tags_at(buf, "the paper")
    assert "link" in names
    (link_tag,) = [n for n in names if n in links]
    assert links[link_tag] == "https://doi.org/10.1/x"


# ---- hostile / odd input ------------------------------------------

def test_raw_html_is_literal_text():
    buf, _ = _render("<script>alert(1)</script> and <b>x</b>")
    assert "<script>alert(1)</script>" in _text(buf)
    assert "<b>x</b>" in _text(buf)


def test_unbalanced_markers_do_not_crash():
    buf, _ = _render("***species** and *open and `tick")
    assert "species" in _text(buf)


def test_empty_and_none():
    assert _text(_render("")[0]) == ""
    assert _text(_render(None)[0]) == ""


def test_make_summary_view_is_read_only():
    view = summary_view.make_summary_view("**hi**")
    assert not view.get_editable()
    assert not view.get_cursor_visible()
    assert "alexandria-summary" in view.get_css_classes()
