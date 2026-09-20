"""Find-as-you-type that highlights rather than filters.

A list the user is reading has an order, and that order is often the
information: an author's works run newest-first or most-cited-first, so
"where in the career does this fall?" is answered by position. Removing
the rows that do not match answers "which" and destroys "where", which
is why this exists beside the library's filtering search rather than
instead of it. The model is a browser's Ctrl+F.

GTK-free on purpose: the marking is Pango markup, but nothing here
needs a display, and the reference list in the viewer and "Cited most
often by" want the same behaviour.
"""

import re

from .markup import _markup_parses, safe_pango_markup

# The one yellow the application means "found" with: the viewer's
# highlight fill (RGB 1.0, 0.95, 0.0) and the card's comment-count
# chip. Black text on top, because a light yellow behind a dim or
# coloured label can otherwise fall below a readable contrast.
HIGHLIGHT_BG = "#fff200"
HIGHLIGHT_FG = "#000000"

# Below this, matching is more noise than help: one letter matches
# most rows, and the point of the count is to be worth reading.
MIN_QUERY = 2


def spans(text, query):
    """Non-overlapping `(start, end)` matches of `query` in `text`,
    case-insensitively. Empty for a query shorter than `MIN_QUERY`."""
    if not text or not query or len(query.strip()) < MIN_QUERY:
        return []
    needle = query.strip()
    return [(m.start(), m.end())
            for m in re.finditer(re.escape(needle), text, re.IGNORECASE)]


def count(text, query):
    return len(spans(text, query))


_TAG_RE = re.compile(r"<[^>]*>")


def highlight(text, query):
    """`text` as Pango markup with every match given the found
    background, safe to place inside another span — which is how
    callers keep their own colour, size and weight around it.

    The text is escaped **once**, as a whole, and the match is then
    looked for inside the escaped string. Escaping fragment by
    fragment instead loses whitespace at the cuts (measured:
    "H<sub>2</sub>O in cryo-EM" came back with the space before
    "cryo" gone and two spaces added around the tag), because
    `safe_pango_markup` normalises what it is given.

    Two consequences of matching escaped text, both wanted: a query
    with an "&" or "<" in it is escaped the same way and so still
    matches, and a match that overlaps one of the inline tags
    `safe_pango_markup` preserves — `<i>`, `<sub>` and friends — is
    skipped rather than allowed to cut the tag in half."""
    esc = safe_pango_markup(text or "")
    needle = safe_pango_markup((query or "").strip())
    if not needle or len((query or "").strip()) < MIN_QUERY:
        return esc
    tags = [m.span() for m in _TAG_RE.finditer(esc)]
    out, prev = [], 0
    for m in re.finditer(re.escape(needle), esc, re.IGNORECASE):
        s_i, e_i = m.span()
        if any(s_i < t_end and t_start < e_i for t_start, t_end in tags):
            continue
        out.append(esc[prev:s_i])
        out.append('<span background="{}" foreground="{}">{}</span>'.format(
            HIGHLIGHT_BG, HIGHLIGHT_FG, esc[s_i:e_i]))
        prev = e_i
    if not out:
        return esc
    out.append(esc[prev:])
    marked = "".join(out)
    # Never a stray tag and never a crash, at the cost of one match
    # going unmarked — the bargain `markdown_to_pango` makes too.
    return marked if _markup_parses(marked) else esc


def matching_indices(texts_per_row, query):
    """Indices of the rows with at least one match, in order.

    `texts_per_row` is one iterable of strings per row — the fields
    that are *on screen*. Searching text the reader cannot see (an
    abstract that is fetched but not shown) produces matches that look
    like false positives, so callers pass what is visible."""
    out = []
    for i, texts in enumerate(texts_per_row):
        if any(spans(t, query) for t in texts):
            out.append(i)
    return out


def summary(n_matching, n_rows, query):
    """The line beside the entry: how much of the list matched.

    Says nothing for an empty or too-short query — a count that
    appears before there is anything to count reads as a result."""
    if not query or len(query.strip()) < MIN_QUERY:
        return ""
    if not n_matching:
        return "no matches"
    return "{} of {}".format(n_matching, n_rows)
