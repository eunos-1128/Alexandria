"""Rich rendering of Markdown summaries into a Gtk.TextView.

The summary popover used to show a Gtk.Label fed by
`markup.markdown_to_pango`, a line-by-line regex subset. A label has
no paragraph spacing, no margins and no full-width backgrounds, and a
line-based subset can't do nesting, emphasis spanning a line break or
soft breaks — so summaries read flat. This module parses with
markdown-it-py (CommonMark, plus GFM tables and strikethrough) and
renders into a read-only TextView with TextTags.

The tag styling lives in `summary_style.json` next to this module:
TextTag properties by tag name, with any value that differs by theme
written as {"light": ..., "dark": ...} (null = leave unset in that
theme). The styling and layout rules (heading scale/colour ramp,
hanging list indents, code / quote paragraph backgrounds, plain-text
tables) are adapted from Manuscript's renderer
(https://gitlab.com/ilshat-apps/manuscript, GPL-3.0-or-later).

Raw HTML is disabled in the parser, so `<script>` or any other tag in a
summary shows as literal text — the TextView never interprets markup,
so there is nothing to escape and nothing that can fail to parse.
"""

import json
import os
import unicodedata

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gdk, Gtk, Pango  # noqa: E402

from markdown_it import MarkdownIt  # noqa: E402

# "html": False turns raw HTML into plain text tokens; linkify is off
# because it needs an extra package and summaries rarely carry bare URLs.
_MD = MarkdownIt("commonmark", {"html": False}).enable(
    ["table", "strikethrough"])

_CSS_CLASS = "alexandria-summary"
_css_installed = False

_STYLE_PATH = os.path.join(os.path.dirname(__file__), "summary_style.json")
_style_cache = None

# JSON can't carry Pango enums; these properties are written by name.
_ENUM_PROPS = {"style": Pango.Style, "underline": Pango.Underline}


def _style():
    global _style_cache
    if _style_cache is None:
        with open(_STYLE_PATH, encoding="utf-8") as f:
            _style_cache = json.load(f)
    return _style_cache


def _tag_props(spec, dark):
    """Resolve one tag's JSON spec to TextTag properties for a theme."""
    props = {}
    for key, value in spec.items():
        if isinstance(value, dict):
            value = value.get("dark" if dark else "light")
        if value is None:
            continue
        if key in _ENUM_PROPS:
            value = getattr(_ENUM_PROPS[key], value.upper())
        props[key] = value
    return props


def _list_tag(buffer, depth, dark):
    """Hanging-indent tag for list items at `depth` (1 = top level),
    created on first use so any nesting depth works. `list-item` in the
    style file is the top level; each level deeper indents by
    `list_indent_step`."""
    name = "list-item-{}".format(depth)
    tag = buffer.get_tag_table().lookup(name)
    if tag is None:
        style = _style()
        props = _tag_props(style["tags"]["list-item"], dark)
        props["left_margin"] = (props.get("left_margin", 0)
                                + style["list_indent_step"] * (depth - 1))
        tag = buffer.create_tag(name, **props)
    return tag


def _display_width(s):
    """Monospace column width: wide East Asian characters take two."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in s)


class _Renderer:
    """Walks markdown-it block tokens and inserts styled text at the end
    of the buffer. Every block lives on its own buffer line, so the
    block's paragraph-level tag (spacing, margins, background) covers
    exactly that block."""

    def __init__(self, buffer, dark):
        self.buf = buffer
        self.dark = dark
        self.links = {}          # link tag name -> URL
        self.lists = []          # per open list: [ordered, next_number]
        self.quote_depth = 0
        self.pending_bullet = False
        self.block_tags = []     # tag names for the current block's text
        self.table = None        # rows of cell strings while in a table
        self.row = None
        for name, spec in _style()["tags"].items():
            if name != "list-item":          # per depth, see _list_tag
                buffer.create_tag(name, **_tag_props(spec, dark))

    # -- insertion -----------------------------------------------------

    def _insert(self, text, names=(), extra=()):
        table = self.buf.get_tag_table()
        tags = [table.lookup(n) for n in names] + list(extra)
        self.buf.insert_with_tags(self.buf.get_end_iter(), text, *tags)

    def _context(self):
        """Tags every piece of text inherits from its enclosing blocks."""
        tags = []
        if self.quote_depth:
            tags.append(self.buf.get_tag_table().lookup("blockquote"))
        if self.lists:
            tags.append(_list_tag(self.buf, len(self.lists), self.dark))
        return tags

    def _begin_block(self):
        # The first block inside a list item shares the bullet's line.
        if self.pending_bullet:
            self._emit_bullet()
            return
        if self.buf.get_char_count() > 0:
            self._insert("\n")

    def _emit_bullet(self):
        self.pending_bullet = False
        ordered, number = self.lists[-1]
        if ordered:
            marker = "{}. ".format(number)
            self.lists[-1][1] += 1
        else:
            marker = "• " if len(self.lists) == 1 else "◦ "
        self._insert(marker, extra=self._context())

    # -- inline --------------------------------------------------------

    def _inline(self, children):
        base = self._context()
        style = []               # names of active inline tags
        link = None              # the current link's tag, if any
        for tok in children or []:
            t = tok.type
            if t == "text":
                self._emit(tok.content, base, style, link)
            elif t == "softbreak":
                # CommonMark: a newline inside a paragraph is a space.
                self._emit(" ", base, style, link)
            elif t == "hardbreak":
                self._emit("\n", base, style, link)
            elif t == "code_inline":
                self._emit(tok.content, base, style + ["inline-code"], link)
            elif t in ("strong_open", "em_open", "s_open"):
                style.append({"strong_open": "bold", "em_open": "italic",
                              "s_open": "strikethrough"}[t])
            elif t in ("strong_close", "em_close", "s_close"):
                name = {"strong_close": "bold", "em_close": "italic",
                        "s_close": "strikethrough"}[t]
                if name in style:
                    style.remove(name)
            elif t == "link_open":
                name = "link-{}".format(len(self.links))
                link = self.buf.create_tag(name)
                self.links[name] = tok.attrGet("href") or ""
                style.append("link")
            elif t == "link_close":
                link = None
                if "link" in style:
                    style.remove("link")
            elif t == "image":
                # No images in a popover; keep the alt text readable.
                self._emit(tok.content, base, style + ["italic"], link)

    def _emit(self, text, base, style, link):
        extra = base + self._block_tag_objs()
        if link is not None:
            extra.append(link)
        self._insert(text, style, extra)

    def _block_tag_objs(self):
        table = self.buf.get_tag_table()
        return [table.lookup(n) for n in self.block_tags]

    # -- tables --------------------------------------------------------

    @staticmethod
    def _cell_text(children):
        parts = []
        for tok in children or []:
            if tok.type in ("text", "code_inline", "image"):
                parts.append(tok.content)
            elif tok.type in ("softbreak", "hardbreak"):
                parts.append(" ")
        return "".join(parts).strip()

    def _render_table(self, rows):
        ncols = max((len(r) for r in rows), default=0)
        if not ncols:
            return
        widths = [3] * ncols
        for row in rows:
            for i, cell in enumerate(row):
                widths[i] = max(widths[i], _display_width(cell))

        def line(row):
            cells = []
            for i, w in enumerate(widths):
                cell = row[i] if i < len(row) else ""
                cells.append(" " + cell + " " * (w - _display_width(cell) + 1))
            return "|" + "|".join(cells) + "|"

        sep = "|" + "+".join("-" * (w + 2) for w in widths) + "|"
        ctx = self._context()
        for i, row in enumerate(rows):
            self._begin_block()
            names = ["table", "table-header"] if i == 0 else ["table"]
            self._insert(line(row), names, ctx)
            if i == 0:
                self._begin_block()
                self._insert(sep, ["table"], ctx)

    # -- blocks --------------------------------------------------------

    def render(self, text):
        for tok in _MD.parse(text or ""):
            t = tok.type
            if self.table is not None and t not in ("table_close",):
                if t == "tr_open":
                    self.row = []
                elif t == "tr_close":
                    self.table.append(self.row)
                elif t == "inline":
                    self.row.append(self._cell_text(tok.children))
                continue
            if t == "heading_open":
                self._begin_block()
                self.block_tags = [tok.tag]          # "h1".."h6"
            elif t == "paragraph_open":
                self._begin_block()
                self.block_tags = [] if self.lists else ["paragraph"]
            elif t in ("heading_close", "paragraph_close"):
                self.block_tags = []
            elif t == "inline":
                self._inline(tok.children)
            elif t in ("fence", "code_block"):
                self._begin_block()
                self._insert(tok.content.rstrip("\n"), ["code-block"],
                             self._context())
            elif t == "hr":
                self._begin_block()
                self._insert("─" * 24, ["rule"], self._context())
            elif t == "blockquote_open":
                self.quote_depth += 1
            elif t == "blockquote_close":
                self.quote_depth -= 1
            elif t == "bullet_list_open":
                self.lists.append([False, 1])
            elif t == "ordered_list_open":
                start = tok.attrGet("start")
                self.lists.append([True, int(start) if start else 1])
            elif t in ("bullet_list_close", "ordered_list_close"):
                self.lists.pop()
            elif t == "list_item_open":
                self._begin_block()
                self.pending_bullet = True
            elif t == "list_item_close":
                if self.pending_bullet:              # empty item
                    self._emit_bullet()
            elif t == "table_open":
                self.table = []
            elif t == "table_close":
                rows, self.table = self.table, None
                self._render_table(rows)
            elif t == "html_block":
                self._begin_block()
                self._insert(tok.content.rstrip("\n"), ["paragraph"],
                             self._context())
        return self.links


def render_markdown(buffer, text, dark=False):
    """Replace `buffer`'s contents with `text` rendered as Markdown.

    Returns {link tag name: URL} for the links it created. Expects a
    fresh buffer: tags are created, not reused."""
    buffer.set_text("")
    return _Renderer(buffer, dark).render(text)


def _ensure_css():
    """A TextView paints the document background; in a popover that
    shows as a box. Make summary views transparent, once per process."""
    global _css_installed
    if _css_installed:
        return
    display = Gdk.Display.get_default()
    if display is None:
        return
    provider = Gtk.CssProvider()
    provider.load_from_string(
        "textview.{0}, textview.{0} > text {{ background: transparent; }}"
        .format(_CSS_CLASS))
    Gtk.StyleContext.add_provider_for_display(
        display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _css_installed = True


def _link_at(view, links, x, y):
    bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET,
                                          int(x), int(y))
    found, it = view.get_iter_at_location(bx, by)
    if not found:
        return None
    for tag in it.get_tags():
        url = links.get(tag.props.name or "")
        if url:
            return url
    return None


def _open_uri(view, uri):
    try:
        Gtk.UriLauncher.new(uri).launch(view.get_root(), None, None)
        return
    except (AttributeError, TypeError, GLib.Error):
        pass
    from . import opener
    opener.open_external(uri)


def _install_link_handling(view, links):
    """Click opens a link; the pointer becomes a hand over one. A click
    that ends a drag-selection doesn't count."""
    if not links:
        return

    click = Gtk.GestureClick()

    def on_released(gesture, n_press, x, y):
        if n_press != 1 or view.get_buffer().get_has_selection():
            return
        url = _link_at(view, links, x, y)
        if url:
            _open_uri(view, url)

    click.connect("released", on_released)
    view.add_controller(click)

    motion = Gtk.EventControllerMotion()
    motion.connect(
        "motion",
        lambda _c, x, y: view.set_cursor_from_name(
            "pointer" if _link_at(view, links, x, y) else "text"))
    view.add_controller(motion)


def make_summary_view(text, dark=False):
    """A read-only, selectable, transparent TextView showing `text`
    rendered as Markdown. The caller sets its width (a wrapping
    TextView, like a wrapping label, has a tiny minimum width)."""
    _ensure_css()
    view = Gtk.TextView(editable=False, cursor_visible=False,
                        wrap_mode=Gtk.WrapMode.WORD_CHAR)
    view.add_css_class(_CSS_CLASS)
    links = render_markdown(view.get_buffer(), text, dark)
    _install_link_handling(view, links)
    return view
