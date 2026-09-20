"""Window listing an author's recent works from OpenAlex, with a small
citations-per-year histogram in the header.

Opened from the authors-popover "find more by author" button.
"""

import os
import threading
import urllib.error
import urllib.parse
import urllib.request

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Gdk, Gio, Pango, Adw, GObject

import datetime

from . import (metrics, index, importer, opener, author_image,
               viewer, pdf_fetch, status_ticker, funding, gtr, gestures,
               find_text)
from .identity import user_agent
from .markup import safe_pango_markup


def _attach_copy_link_menu(button, url):
    """Attach a context menu with a 'Copy link' entry.
    The button keeps its primary (left-click) action; a right-click or
    a press-and-hold pops up a small menu over the button."""
    if not url:
        return
    action = Gio.SimpleAction.new("copy_url", None)

    def do_copy(_a, _p):
        clipboard = button.get_clipboard()
        try:
            clipboard.set(url)
        except Exception:
            pass

    action.connect("activate", do_copy)
    group = Gio.SimpleActionGroup()
    group.add_action(action)
    button.insert_action_group("ctx", group)

    menu = Gio.Menu()
    menu.append("Copy link", "ctx.copy_url")
    popover = Gtk.PopoverMenu.new_from_model(menu)
    popover.set_parent(button)

    def on_menu(_g, x, y):
        rect = Gdk.Rectangle()
        rect.x = int(x)
        rect.y = int(y)
        rect.width = 1
        rect.height = 1
        popover.set_pointing_to(rect)
        popover.popup()

    gestures.add_context_menu(button, on_menu)

from . import prefs as _prefs

# Read on import — same shape as `browse.LIBRARY_ROOT`. Previously
# this module read the env var directly with a `~/pdfs` default, which
# ignored the Preferences-set root and dropped "Add to Archive"
# downloads into `~/pdfs` instead of the real library directory.
LIBRARY_ROOT = _prefs.get_library_root()


def _fmt_compact(n):
    """Render a count as `2.1M`, `337k`, `42` etc. Used in the
    citing-impact chip so three numbers fit on one line."""
    if n is None:
        return "0"
    n = int(n)
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1_000_000)
    if n >= 10_000:
        return "{:.0f}k".format(n / 1_000)
    if n >= 1_000:
        return "{:.1f}k".format(n / 1_000)
    return str(n)


def _author_score_is_fresh(cached):
    """True when a cached `author_scores` row is younger than the
    TTL. Anything older we'll show briefly then refresh."""
    when = (cached or {}).get("computed_at")
    if not when:
        return False
    try:
        d = datetime.date.fromisoformat(when[:10])
    except ValueError:
        return False
    age_days = (datetime.date.today() - d).days
    return age_days < index.AUTHOR_SCORE_TTL_DAYS


def _author_works_cache_is_fresh(cached):
    """True when a cached `author_works_cache` row is younger than
    its TTL. Stale rows are dropped on read so the next switch
    fetches anew."""
    when = (cached or {}).get("computed_at")
    if not when:
        return False
    try:
        d = datetime.date.fromisoformat(when[:10])
    except ValueError:
        return False
    age_days = (datetime.date.today() - d).days
    return age_days < index.AUTHOR_WORKS_TTL_DAYS


# Venue chips. Some OpenAlex `works` results are legitimately
# different in nature from a journal paper (Zenodo uploads can be
# datasets, slides, code archives, or grey-literature drafts; JoVE
# is a video journal; bioRxiv / arXiv are preprints). A small chip
# in the work row lets the user spot these at a glance instead of
# squinting at the journal name. DOI-prefix match is the primary
# signal; journal-name fallback catches the long tail.
#
# `(label, foreground)` tuples — no background fills, no emojis
# (color emojis crash the user's Cairo/CoreText pipeline on macOS).
# The institution line. No leading "·" any more: that separated it
# from the ORCID back when they shared a row.
_INSTITUTION_MARKUP = "<span size='small' alpha='75%'>{}</span>"


def current_affiliation(rows):
    """The row describing where this author is *now*, or None.

    `metrics.fetch_author_profile` sorts most-recent `year_max`
    first, so this is rows[0] — but stated as a function because it
    is the one judgement in the header worth testing on its own, and
    a caller reading `rows[0]` cannot see that the order is load
    bearing."""
    for r in rows or []:
        if r.get("display_name"):
            return r
    return None


def _people_flowbox():
    """The wrapping grid of name buttons used by both people
    sections."""
    box = Gtk.FlowBox()
    box.set_selection_mode(Gtk.SelectionMode.NONE)
    box.set_max_children_per_line(8)
    box.set_row_spacing(4)
    box.set_column_spacing(4)
    box.set_margin_top(4)
    box.set_margin_bottom(4)
    return box


def _section_expander(title, child, pref_key):
    """A collapsible section: a small grey label with a disclosure
    triangle, revealing `child`.

    Collapsed by default — these lists are a dozen names each, and
    two of them together pushed the works list off the bottom of the
    window. The open/closed choice is remembered, so a reader who
    wants them open says so once rather than on every author."""
    exp = Gtk.Expander()
    lbl = Gtk.Label(xalign=0.0)
    lbl.set_markup(
        "<span size='small' alpha='65%'>{}</span>".format(
            GLib.markup_escape_text(title)))
    exp.set_label_widget(lbl)
    exp.set_child(child)
    exp.set_visible(False)
    exp.set_expanded(_prefs.get_section_expanded(pref_key))
    exp.connect(
        "notify::expanded",
        lambda e, _p, k=pref_key: _prefs.set_section_expanded(
            k, e.get_expanded()))
    return exp


def _set_section_note(expander, title, note):
    """Put a word about what is happening in a section's own label.

    These sections are collapsed by default, so the header is the
    only part a reader sees — a spinner inside the body would be
    hidden behind the disclosure triangle."""
    lbl = expander.get_label_widget()
    if lbl is None:
        return
    lbl.set_markup(
        "<span size='small' alpha='65%'>{}  <i>{}</i></span>".format(
            GLib.markup_escape_text(title),
            GLib.markup_escape_text(note)))
    expander.set_visible(True)


def _set_section_count(expander, title, n):
    """Put the count in the section's label, so a collapsed section
    still says how much is behind it."""
    lbl = expander.get_label_widget()
    if lbl is None:
        return
    lbl.set_markup(
        "<span size='small' alpha='65%'>{}  ({})</span>".format(
            GLib.markup_escape_text(title), n))


_VENUE_CHIPS_BY_DOI_PREFIX = (
    ("10.5281/",   ("Zenodo",  "#b87000")),   # muted orange
    ("10.3791/",   ("JoVE",    "#3366aa")),   # muted blue
    ("10.1101/",   ("bioRxiv", "#777777")),
    ("10.48550/",  ("arXiv",   "#777777")),
    ("10.26434/",  ("ChemRxiv", "#777777")),
    ("10.21203/rs", ("Research Square", "#777777")),
    ("10.22541/au", ("Authorea", "#777777")),
    ("10.2139/ssrn", ("SSRN",   "#777777")),
    ("10.31234/",  ("PsyArXiv", "#777777")),
    ("10.31219/",  ("OSF",     "#777777")),
    ("10.20944/",  ("Preprints.org", "#777777")),
    ("10.36227/",  ("TechRxiv", "#777777")),
)
# bioRxiv and medRxiv share the 10.1101/ prefix; the journal name
# is how OpenAlex disambiguates. Apply this *after* the prefix
# match decided "bioRxiv".
_VENUE_NAME_OVERRIDES = {
    "medrxiv": ("medRxiv", "#777777"),
}


def _venue_chip(work):
    """Decide on at most one venue chip for `work`. Returns
    `(label, foreground)` or None.

    Match order: DOI prefix first (cheap, unambiguous), then a
    journal-name override for the bioRxiv/medRxiv split, then a
    journal-name match for entries OpenAlex stored without a DOI
    in the expected prefix."""
    doi = (work.get("doi") or "").lower()
    journal = (work.get("journal") or "")
    journal_low = journal.lower()
    for prefix, chip in _VENUE_CHIPS_BY_DOI_PREFIX:
        if doi.startswith(prefix):
            # 10.1101/ → bioRxiv default, override to medRxiv when
            # OpenAlex labelled it as such.
            for key, alt in _VENUE_NAME_OVERRIDES.items():
                if key in journal_low:
                    return alt
            return chip
    # No DOI prefix match (or no DOI at all) — fall back to
    # journal-name match. Catches deposits that bypass the usual
    # DOI registrars.
    if journal_low.startswith("zenodo"):
        return ("Zenodo", "#b87000")
    if (journal_low.startswith("journal of visualized experiments")
            or journal_low == "jove"):
        return ("JoVE", "#3366aa")
    return None


# Hide the histogram entirely if no year reaches this many citations.
HISTOGRAM_MIN_PEAK = 5

HISTOGRAM_WIDTH = 320
HISTOGRAM_HEIGHT = 90


def _open_url(url):
    opener.open_external(url)


def _filename_for(doi, oa_url):
    """Pick a sensible filename for a downloaded OA PDF. Prefer the URL's
    last path segment when it looks PDF-like; otherwise derive from DOI."""
    if oa_url:
        from urllib.parse import urlparse
        leaf = os.path.basename(urlparse(oa_url).path or "")
        if leaf.lower().endswith(".pdf") and len(leaf) > 4:
            return leaf
    if doi:
        return doi.replace("/", "_") + ".pdf"
    return None


# The download helpers that used to live here (a curl fallback and
# _download_pdf) moved to pdf_fetch, which the MCP server also uses
# and which reports progress. Keeping a second copy here is how the
# GUI came to be a source behind: see the Unpaywall backlog entry.


def _truncate_authors(names, max_chars=110):
    """Join as many whole author names as fit in `max_chars`,
    appending ", et al." when any were dropped. The default budget
    approximates one line of small text across the works pane at
    the default window width — fuller than the old fixed four, and
    still one line for typical name lengths."""
    if not names:
        return ""
    full = ", ".join(names)
    if len(full) <= max_chars:
        return full
    suffix = ", et al."
    shown = []
    used = 0
    for n in names:
        cost = len(n) if not shown else len(n) + 2
        if shown and used + cost + len(suffix) > max_chars:
            break
        shown.append(n)
        used += cost
    return ", ".join(shown) + suffix


def _draw_histogram(area, cr, width, height, counts_by_year):
    """Bars for cited_by_count per year. counts_by_year is oldest-first."""
    if not counts_by_year:
        return
    peak = max(r["cited_by_count"] for r in counts_by_year) or 1

    style = area.get_style_context()
    fg = style.get_color()  # Gdk.RGBA, theme-aware

    # Layout: leave room at bottom for year labels and a touch on top
    # for the peak label.
    pad_top = 14
    pad_bot = 18
    pad_x = 4
    plot_h = max(1, height - pad_top - pad_bot)
    plot_w = max(1, width - 2 * pad_x)

    n = len(counts_by_year)
    # Bar width with a 2px gap between bars.
    gap = 2
    bw = max(2, (plot_w - (n - 1) * gap) / n)

    # Bars.
    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.55)
    for i, r in enumerate(counts_by_year):
        c = r["cited_by_count"]
        if c <= 0:
            continue
        h = plot_h * (c / peak)
        x = pad_x + i * (bw + gap)
        y = pad_top + (plot_h - h)
        cr.rectangle(x, y, bw, h)
        cr.fill()

    # Axis baseline.
    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.35)
    cr.set_line_width(1.0)
    cr.move_to(pad_x, pad_top + plot_h + 0.5)
    cr.line_to(pad_x + plot_w, pad_top + plot_h + 0.5)
    cr.stroke()

    # Year labels (first, last, and the peak year).
    cr.set_source_rgba(fg.red, fg.green, fg.blue, 0.85)
    layout = area.create_pango_layout("")
    fd = Pango.FontDescription("Sans 8")
    layout.set_font_description(fd)

    def _draw_text(text, cx, cy, anchor="center"):
        layout.set_text(text, -1)
        tw, th = layout.get_pixel_size()
        if anchor == "center":
            cr.move_to(cx - tw / 2, cy)
        elif anchor == "left":
            cr.move_to(cx, cy)
        elif anchor == "right":
            cr.move_to(cx - tw, cy)
        from gi.repository import PangoCairo
        PangoCairo.show_layout(cr, layout)

    first_year = counts_by_year[0]["year"]
    last_year = counts_by_year[-1]["year"]
    peak_idx = max(range(n), key=lambda i: counts_by_year[i]["cited_by_count"])
    peak_year = counts_by_year[peak_idx]["year"]

    y_label_y = pad_top + plot_h + 3
    _draw_text(str(first_year),
               pad_x + bw / 2, y_label_y, "left")
    if last_year != first_year:
        _draw_text(str(last_year),
                   pad_x + plot_w - bw / 2, y_label_y, "right")
    if peak_year not in (first_year, last_year):
        cx = pad_x + peak_idx * (bw + gap) + bw / 2
        _draw_text(str(peak_year), cx, y_label_y, "center")

    # Peak count label, above the tallest bar.
    cx = pad_x + peak_idx * (bw + gap) + bw / 2
    _draw_text(str(peak), cx, 0, "center")


def _library_pdf_for_doi(conn, doi):
    """Resolve a DOI to the library's (pdf_path, sidecar_path), or
    None when the paper isn't here as a real file — ghost rows
    (BibTeX imports, metadata-only) have a DOI but no PDF to open."""
    want = index.normalize_doi(doi)
    if not want:
        return None
    want = want.lower()
    try:
        cur = conn.execute(
            "SELECT doi, pdf_path, sidecar_path FROM papers "
            "WHERE doi IS NOT NULL AND doi<>''")
        for row_doi, pdf_path, sc_path in cur:
            d = index.normalize_doi(row_doi)
            if (d and d.lower() == want
                    and pdf_path and os.path.isfile(pdf_path)):
                return pdf_path, sc_path
    except Exception:
        pass
    return None


def _existing_dois(conn):
    """Set of normalized DOIs already in our library, lower-cased."""
    out = set()
    try:
        cur = conn.execute(
            "SELECT doi FROM papers WHERE doi IS NOT NULL AND doi<>''")
        for row in cur:
            d = index.normalize_doi(row[0])
            if d:
                out.add(d.lower())
    except Exception:
        pass
    return out


class AuthorPage(Gtk.Box):
    def __init__(self, conn, authorship, on_institution=None,
                 on_image_changed=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.conn = conn
        self.authorship = authorship or {}
        # Called (on the main thread) with the profile's current
        # affiliation once it arrives. Authors opened via collaborator
        # chips carry institution=None, so this is how the hosting
        # AuthorsWindow learns the affiliation the page fetched and
        # can backfill its sidebar row and the author_trail table.
        self._on_institution = on_institution
        # Called (on the main thread) with the new photo path (or
        # None) after every successful set/fetch/remove, so the
        # hosting window can refresh a sidebar row's avatar too.
        self._on_image_changed = on_image_changed
        name = self.authorship.get("name") or "Unknown author"

        self.set_margin_start(12)
        self.set_margin_end(12)
        self.set_margin_top(12)
        self.set_margin_bottom(12)

        # --- Header (name, ORCID, headline numbers) -------------------
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)

        # Author photo — silhouette/initials disc until an image is
        # set. Click for fetch/choose/remove; also a drop target for
        # files and browser-dragged images. Circular, which is
        # Adw.Avatar's whole shape and the platform convention; the
        # stored image is up to 512px on its long side
        # (author_image.MAX_SIDE), so there is headroom to grow this.
        self.avatar = Adw.Avatar.new(80, name or None, True)
        # Top-anchored, not centred: profile data arriving later
        # (wrapped institution, citing-impact line, the +N button)
        # can grow the header, and a centred avatar visibly slides
        # down mid-load. Anchored, growth only adds space below.
        self.avatar.set_valign(Gtk.Align.START)
        self._avatar_menu = self._build_avatar_menu()
        self._avatar_menu.set_parent(self.avatar)
        # Either mouse button opens the menu: it is semantically a
        # context menu (operations on the photo), and an image-like
        # widget invites right-click; left-click stays for
        # discoverability. Button 0 = listen to all, filter below.
        click = Gtk.GestureClick.new()
        click.set_button(0)
        click.connect(
            "released",
            lambda g, *_a: self._avatar_menu.popup()
            if g.get_current_button() in (Gdk.BUTTON_PRIMARY,
                                          Gdk.BUTTON_SECONDARY)
            else None)
        self.avatar.add_controller(click)
        self.avatar.set_tooltip_text(
            "Author photo — click to add or change")
        self.avatar.set_cursor(Gdk.Cursor.new_from_name("pointer"))
        drop = Gtk.DropTarget.new(GObject.TYPE_NONE,
                                  Gdk.DragAction.COPY)
        drop.set_gtypes([Gdk.FileList, str])
        drop.connect("drop", self._on_avatar_drop)
        self.avatar.add_controller(drop)
        header.append(self.avatar)
        self._set_avatar_from_disk()

        hleft = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        hleft.set_hexpand(True)
        name_lbl = Gtk.Label(xalign=0.0)
        name_lbl.set_markup(
            "<span size='x-large' weight='bold'>{}</span>".format(
                GLib.markup_escape_text(name)))
        hleft.append(name_lbl)

        # Sub line: ORCID · current institution + small history button
        # that opens a popover with the full prior-institution list.
        # The label gets filled in once the profile arrives — for
        # callers that don't pass `institution` in the authorship
        # (e.g. Discover) the line stays sparse until then.
        sub_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self._sub_orcid_lbl = Gtk.Label(xalign=0.0)
        self._sub_orcid_lbl.set_selectable(True)
        self._sub_orcid_lbl.set_visible(False)
        if self.authorship.get("orcid"):
            self._sub_orcid_lbl.set_markup(
                "<span size='small' alpha='75%'>ORCID {}</span>".format(
                    GLib.markup_escape_text(self.authorship["orcid"])))
            self._sub_orcid_lbl.set_visible(True)
        sub_row.append(self._sub_orcid_lbl)

        # The institution gets its own row between the name and the
        # ORCID, rather than sharing the ORCID's line. Sharing it
        # left the label a narrow column between the ORCID and the
        # history button, so "St. Jude Children's Research Hospital"
        # wrapped into four squashed vertical lines. On its own row
        # it has the width of the header to flow into.
        inst_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                           spacing=6)
        inst_row.set_margin_top(2)
        inst_row.set_margin_bottom(2)
        self._sub_inst_lbl = Gtk.Label(xalign=0.0)
        self._sub_inst_lbl.set_visible(False)
        # Long affiliations (especially CrossRef's full department +
        # school + university + city strings) would otherwise force the
        # whole window wide. Wrap on word/comma boundaries and cap the
        # requested width so the line flows to multiple rows instead.
        self._sub_inst_lbl.set_wrap(True)
        self._sub_inst_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Claim the row rather than taking a natural width: without
        # this the label is allocated close to its longest word and
        # wraps into a narrow column even with the header's width
        # going spare. max_width_chars then only bounds what it may
        # *ask* for, so a very long CrossRef affiliation still cannot
        # stretch the window.
        self._sub_inst_lbl.set_hexpand(True)
        self._sub_inst_lbl.set_max_width_chars(72)
        if self.authorship.get("institution"):
            self._sub_inst_lbl.set_markup(
                _INSTITUTION_MARKUP.format(
                    GLib.markup_escape_text(self.authorship["institution"])))
            self._sub_inst_lbl.set_visible(True)
        inst_row.append(self._sub_inst_lbl)

        # Deliberately not "flat": a flat MenuButton draws no frame
        # until hover, so it reads as a decoration rather than a
        # control. The framed look plus a "+N" count (set in
        # _populate_affiliations) makes it look clickable.
        self._aff_history_btn = Gtk.MenuButton()
        self._aff_history_btn.set_icon_name("document-open-recent-symbolic")
        self._aff_history_btn.set_valign(Gtk.Align.CENTER)
        self._aff_history_btn.set_tooltip_text(
            "Show all prior institutions")
        self._aff_history_btn.set_visible(False)
        self._aff_history_btn.set_halign(Gtk.Align.END)
        inst_row.append(self._aff_history_btn)

        # Clear any auto-selection once after present (selectable
        # labels grab focus and select-all by default).
        GLib.idle_add(
            lambda: (self._sub_orcid_lbl.select_region(0, 0), False)[1])
        hleft.append(inst_row)
        hleft.append(sub_row)

        self.stats_lbl = Gtk.Label(xalign=0.0)
        self.stats_lbl.set_markup("<span size='small' alpha='65%'>Loading…</span>")
        hleft.append(self.stats_lbl)

        # Citing-impact chip: three-bucket (software/method/idea)
        # rollup of citations to this author's works, computed
        # lazily and cached for 30 days in `author_scores`. Hidden
        # until we have an OpenAlex ID and a result to show.
        self.citing_impact_lbl = Gtk.Label(xalign=0.0)
        self.citing_impact_lbl.set_use_markup(True)
        self.citing_impact_lbl.set_visible(False)
        hleft.append(self.citing_impact_lbl)
        # Determinate, unlike the fetch bar above: this walk knows
        # how many works it has to get through before it starts, so
        # a fraction here is a fact rather than a guess. It is also
        # the only thing in the app slow enough to need one —
        # minutes, for a prolific author.
        self.impact_bar = Gtk.ProgressBar()
        self.impact_bar.set_size_request(90, -1)
        self.impact_bar.set_valign(Gtk.Align.CENTER)
        self.impact_bar.set_visible(False)
        hleft.append(self.impact_bar)

        header.append(hleft)

        self.hist_area = Gtk.DrawingArea()
        self.hist_area.set_content_width(HISTOGRAM_WIDTH)
        self.hist_area.set_content_height(HISTOGRAM_HEIGHT)
        # Same top anchor as the avatar: without it the drawing
        # area is stretched to the header's full height, so the
        # bars re-stretch whenever late content grows the header.
        self.hist_area.set_valign(Gtk.Align.START)
        # Always visible, drawing nothing until data arrives: its
        # 90px is what makes the header its final height, so hiding
        # it here made the whole header grow (and the centred
        # avatar visibly drop) the moment the histogram appeared.
        self._hist_data = []
        self.hist_area.set_draw_func(self._on_draw_hist)
        header.append(self.hist_area)

        self.append(header)

        # Avatar/photo workflow status ("Browser opened — drag…",
        # "Looking up Wikidata portrait…"). Lives just under the
        # header so it reads as being about the avatar, not the
        # works list; hidden unless there is something to say.
        self._avatar_status_lbl = Gtk.Label(xalign=0.0)
        self._avatar_status_lbl.set_visible(False)
        self.append(self._avatar_status_lbl)

        # Frequent collaborators (populated async). Collapsible: a
        # dozen names each, twice over, pushed the works list — the
        # reason the page exists — off the bottom of the window.
        self.coauth_box = _people_flowbox()
        self.coauth_label = _section_expander(
            "Frequent collaborators", self.coauth_box, "collaborators")
        self.append(self.coauth_label)

        # "Cited most often by" (populated async, cached 30 days).
        # The mirror of the collaborators row: who this author works
        # with, then who reads them. Both are the social shape of a
        # career, which is the part of a citation graph that carries
        # meaning to a reader.
        self.citers_box = _people_flowbox()
        self.citers_label = _section_expander(
            "Cited most often by", self.citers_box, "citers")
        self.append(self.citers_label)

        # Funders on the papers held locally. No network: OpenAlex
        # already put `funders`/`grants` on each work at import, and
        # the index carries both columns.
        #
        # Funders lead and award numbers are relegated to the
        # tooltip, because the two are not equally trustworthy.
        # Measured across this library: of 85 papers carrying two or
        # more award numbers, 13 attach at least one to the wrong
        # funder — an MRC row holding an EPSRC number, a Wellcome
        # number filed under an Ontario ministry. CrossRef deposits
        # funders and awards as parallel arrays and they arrive
        # mis-zipped. The funder list itself survives that.
        self.funding_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                   spacing=2)
        self.funding_label = _section_expander(
            "Funders on the papers you have", self.funding_box, "funding")
        self.append(self.funding_label)

        # UKRI grants held by this person (GtR, cached 30 days). The
        # companion to the section above and the answer to the
        # question it cannot ask: the one above is funders named on
        # papers, this is awards the person was actually PI or Co-I
        # on. Hidden unless GtR has them — most authors are not
        # UK-funded and an empty "no grants" row would read as a
        # statement about their funding rather than about coverage.
        self.gtr_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                               spacing=4)
        self.gtr_label = _section_expander(
            "UKRI grants", self.gtr_box, "ukri_grants")
        self.append(self.gtr_label)

        self.append(Gtk.Separator())

        # --- Sort toggle: most-recent vs most-cited -------------------
        self._works_sort = "recent"
        self._coauths = None
        sort_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        sort_row.set_halign(Gtk.Align.START)
        self._sort_recent_btn = Gtk.ToggleButton(label="Most recent")
        self._sort_cited_btn = Gtk.ToggleButton(label="Most cited")
        # Linked group so they look like a segmented control.
        sort_row.add_css_class("linked")
        self._sort_recent_btn.set_active(True)
        # Group the toggles so exactly one is active at a time.
        self._sort_cited_btn.set_group(self._sort_recent_btn)
        self._sort_recent_btn.connect("toggled", self._on_sort_toggled, "recent")
        self._sort_cited_btn.connect("toggled", self._on_sort_toggled, "cited")
        sort_row.append(self._sort_recent_btn)
        sort_row.append(self._sort_cited_btn)

        # Refresh button — drops both sort caches for this author
        # and re-fetches. Sits to the right of the segmented sort
        # control with a small gap; icon-only so it doesn't compete
        # for attention with the sort labels.
        refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        refresh_btn.set_tooltip_text(
            "Re-fetch works from OpenAlex (clears the 7-day cache "
            "for this author).")
        refresh_btn.add_css_class("flat")
        refresh_btn.set_margin_start(8)
        refresh_btn.connect("clicked", self._on_refresh_works)
        sort_row.append(refresh_btn)

        self.append(sort_row)

        # --- Find in these works --------------------------------------
        # Highlights, never filters. The list's order is the
        # information — newest first, or most cited first — so hiding
        # the rows that do not match would answer "which" at the cost
        # of "where in the career", which is usually the question. See
        # find_text.
        find_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self._find_entry = Gtk.SearchEntry()
        self._find_entry.set_placeholder_text("Find in these works")
        self._find_entry.set_width_chars(22)
        self._find_entry.connect("search-changed", self._on_find_changed)
        # Enter walks the matches, like a browser's find bar.
        self._find_entry.connect("activate", lambda _e: self._step_find(1))
        find_row.append(self._find_entry)
        self._find_count = Gtk.Label(xalign=0.0)
        self._find_count.add_css_class("dim-label")
        find_row.append(self._find_count)
        self._find_prev_btn = Gtk.Button.new_from_icon_name("go-up-symbolic")
        self._find_prev_btn.add_css_class("flat")
        self._find_prev_btn.set_tooltip_text("Previous match")
        self._find_prev_btn.connect("clicked", lambda _b: self._step_find(-1))
        self._find_next_btn = Gtk.Button.new_from_icon_name("go-down-symbolic")
        self._find_next_btn.add_css_class("flat")
        self._find_next_btn.set_tooltip_text("Next match")
        self._find_next_btn.connect("clicked", lambda _b: self._step_find(1))
        for b in (self._find_prev_btn, self._find_next_btn):
            b.set_visible(False)
            find_row.append(b)
        self.append(find_row)
        # One entry per row on screen: the widgets to re-mark, and the
        # plain text to match against.
        self._find_rows = []
        self._find_matches = []
        self._find_at = -1

        # --- Status + results list ------------------------------------
        # The status line and, beside it, a small bar that pulses
        # while OpenAlex is being waited on.
        #
        # Pulsing rather than filling, because there is no honest
        # fraction to show. Measured on a prolific author: 2.07s
        # across three requests, of which roughly a third is bytes
        # arriving and the rest is waiting for a reply. A bar that
        # tracked bytes would sit at zero for most of the wait and
        # then jump — which is exactly the "is it stalled?" question
        # it was meant to answer.
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                             spacing=8)
        self.status = Gtk.Label(xalign=0.0)
        self.status.set_markup(
            "<span alpha='75%'>Loading from OpenAlex…</span>")
        status_row.append(self.status)
        self.load_bar = Gtk.ProgressBar()
        self.load_bar.set_size_request(90, -1)
        self.load_bar.set_valign(Gtk.Align.CENTER)
        self.load_bar.set_visible(False)
        status_row.append(self.load_bar)

        # One dot per request. The pulse says "something is
        # happening"; these say *what*, and which part is still
        # outstanding — with three calls in flight, a single
        # indicator cannot show that two came back and one did not.
        self.req_dots = {}
        dots = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=3)
        for key, label in (("profile", "profile"),
                           ("works", "works"),
                           ("coauths", "collaborators")):
            dot = Gtk.Label()
            dot.set_valign(Gtk.Align.CENTER)
            self.req_dots[key] = (dot, label)
            dots.append(dot)
        self._dots_box = dots
        dots.set_visible(False)
        status_row.append(dots)
        self.append(status_row)
        self._load_pulse_id = None

        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                spacing=10)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_child(self.list_box)
        self.append(scrolled)

        self._existing = _existing_dois(self.conn)
        self._spawn_fetch()

    # --- Fetch ---------------------------------------------------------

    # Idle, in flight, answered, failed. The greens and oranges are
    # the ones already used for chips elsewhere in the app.
    _REQ_COLOURS = {
        "idle":     ("#999999", "not started"),
        "sent":     ("#cc8800", "waiting for a reply"),
        "received": ("#338033", "reply received"),
        "failed":   ("#cc3333", "failed"),
    }

    def set_request_state(self, key, state):
        """Colour the dot for one of the three OpenAlex calls."""
        entry = getattr(self, "req_dots", {}).get(key)
        if entry is None:
            return False
        dot, label = entry
        colour, meaning = self._REQ_COLOURS.get(
            state, self._REQ_COLOURS["idle"])
        dot.set_markup(
            "<span foreground='{}'>●</span>".format(colour))
        dot.set_tooltip_text("{}: {}".format(label, meaning))
        return False

    def _start_load_pulse(self):
        """Show the activity bar. Idempotent: several fetches can be
        in flight (works, impact, citers) and the last to finish
        stops it."""
        if getattr(self, "load_bar", None) is None:
            return
        self.load_bar.set_visible(True)
        if getattr(self, "_dots_box", None) is not None:
            for key in self.req_dots:
                self.set_request_state(key, "idle")
            self._dots_box.set_visible(True)
        if self._load_pulse_id is None:
            # 120ms: fast enough to read as motion, slow enough that
            # it is not a strobe.
            self._load_pulse_ticks = 0
            self._load_pulse_id = GLib.timeout_add(
                120, self._pulse_load_bar)

    # 120ms x 500 = a minute. A fetch that has not returned by then
    # is not going to, and a timer that pulses forever would keep the
    # page alive with it.
    _MAX_PULSE_TICKS = 500

    def _pulse_load_bar(self):
        if getattr(self, "load_bar", None) is None:
            self._load_pulse_id = None
            return False
        self._load_pulse_ticks = getattr(self, "_load_pulse_ticks", 0) + 1
        if self._load_pulse_ticks > self._MAX_PULSE_TICKS:
            self._load_pulse_id = None
            self.load_bar.set_visible(False)
            return False
        self.load_bar.pulse()
        return True

    def _stop_load_pulse(self):
        if self._load_pulse_id is not None:
            try:
                GLib.source_remove(self._load_pulse_id)
            except Exception:
                pass
            self._load_pulse_id = None
        if getattr(self, "load_bar", None) is not None:
            self.load_bar.set_visible(False)
        if getattr(self, "_dots_box", None) is not None:
            self._dots_box.set_visible(False)
        return False

    def _spawn_fetch(self):
        orcid = self.authorship.get("orcid")
        oa_id = self.authorship.get("openalex_id")
        self._start_load_pulse()
        # Local and cheap, so it fills before the network sections
        # rather than after them.
        self._fill_funding()
        threading.Thread(target=self._do_gtr, daemon=True).start()
        threading.Thread(
            target=self._do_fetch, args=(orcid, oa_id),
            daemon=True).start()
        # Citing-impact lives on its own thread because the compute
        # is slow (~3 min for prolific authors) and we don't want
        # to block the works list on it. Cache-hits return in
        # microseconds; cache-misses do the slow OpenAlex walk.
        if oa_id:
            threading.Thread(
                target=self._do_citing_impact, args=(oa_id,),
                daemon=True).start()
        # "Cited most often by" — two OpenAlex calls, cached 30 days,
        # so this shows straight away on a revisit and takes a couple
        # of seconds the first time.
        self._start_citers_load()

    def _do_fetch(self, orcid, oa_id):
        """Profile, works and collaborators — three independent
        requests, so run them together.

        Measured on a prolific author: 2.07s one after another
        (0.20 + 0.97 + 0.90), 1.41s side by side. The page cannot
        render until all three are in, so the sequence was costing
        two thirds of a second for nothing."""
        out = {}

        def call(key, fn):
            GLib.idle_add(self.set_request_state, key, "sent")
            try:
                out[key] = fn()
            except Exception as e:
                print("author fetch ({}): {}".format(key, e))
                out[key] = None
                GLib.idle_add(self.set_request_state, key, "failed")
                return
            GLib.idle_add(self.set_request_state, key, "received")

        threads = [
            threading.Thread(target=call, args=("profile", lambda:
                metrics.fetch_author_profile(
                    orcid=orcid, openalex_id=oa_id)), daemon=True),
            threading.Thread(target=call, args=("works", lambda:
                self._cached_or_fetch_works(
                    orcid, oa_id, self._works_sort)), daemon=True),
            threading.Thread(target=call, args=("coauths", lambda:
                metrics.fetch_coauthors(
                    orcid=orcid, openalex_id=oa_id, limit=12)), daemon=True),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        GLib.idle_add(self._apply_results, out.get("profile"),
                      out.get("works"), out.get("coauths"))

    def _cached_or_fetch_works(self, orcid, oa_id, sort_key):
        """Try the works-list cache first; on hit and fresh, return
        it. Otherwise hit OpenAlex and persist. Cache is keyed off
        the OpenAlex author ID — ORCID-only authors miss the cache,
        but `fetch_works_by_author` then yields the OpenAlex ID via
        its results and we can't backfill cleanly here. Acceptable
        v1: ORCID-only callers always go to the API."""
        if oa_id:
            try:
                cached = index.get_author_works_cache(
                    self.conn, oa_id, sort_key)
            except Exception:
                cached = None
            if cached and _author_works_cache_is_fresh(cached):
                return cached["works"]
        works = metrics.fetch_works_by_author(
            orcid=orcid, openalex_id=oa_id, limit=50, sort=sort_key)
        if oa_id and works:
            try:
                index.set_author_works_cache(
                    self.conn, oa_id, sort_key, works)
            except Exception as e:
                print("[author_works] cache write failed:", e)
        return works

    # --- Citing-impact (cached) ----------------------------------------

    def _do_citing_impact(self, openalex_id):
        cached = index.get_author_score(self.conn, openalex_id)
        if cached and _author_score_is_fresh(cached):
            GLib.idle_add(self._apply_citing_impact, cached, False)
            return
        # Stale or absent — show a "computing" placeholder, then
        # the real numbers. Stale rows are still shown briefly so
        # the user has something to look at while the refresh runs.
        if cached:
            GLib.idle_add(self._apply_citing_impact, cached, True)
        else:
            GLib.idle_add(self._show_citing_impact_pending)
        result = metrics.compute_citing_impact(
            openalex_id, exclude_self_cites=True, polite_delay=0.0,
            on_progress=self._on_impact_progress)
        GLib.idle_add(self._hide_impact_bar)
        if not result:
            GLib.idle_add(self._hide_citing_impact_pending)
            return
        try:
            index.set_author_score(self.conn, openalex_id, result,
                                   self_excluded=True)
        except Exception:
            pass
        GLib.idle_add(self._apply_citing_impact, result, False)

    def _on_impact_progress(self, message, done, total):
        """Called from the compute thread; hop to the main loop."""
        GLib.idle_add(self._apply_impact_progress, message, done, total)

    def _apply_impact_progress(self, message, done, total):
        lbl = getattr(self, "citing_impact_lbl", None)
        bar = getattr(self, "impact_bar", None)
        if lbl is None or bar is None:
            return False
        lbl.set_markup(
            "<span size='small' alpha='55%'>Citing-impact: {}</span>".format(
                GLib.markup_escape_text(message)))
        lbl.set_visible(True)
        if total:
            bar.set_fraction(min(1.0, float(done or 0) / float(total)))
            bar.set_visible(True)
        else:
            # The work list is still being pulled: no denominator
            # yet, so pulse rather than sit at zero.
            bar.set_visible(True)
            bar.pulse()
        return False

    def _hide_impact_bar(self):
        bar = getattr(self, "impact_bar", None)
        if bar is not None:
            bar.set_visible(False)
        return False

    def _show_citing_impact_pending(self):
        self.citing_impact_lbl.set_markup(
            "<span size='small' alpha='55%'>"
            "Citing-impact: computing…"
            "</span>")
        self.citing_impact_lbl.set_visible(True)
        return False

    def _hide_citing_impact_pending(self):
        # Only hide if we're still on the pending placeholder.
        # The user might have a stale-but-rendered result behind us.
        if "computing" in (self.citing_impact_lbl.get_text() or ""):
            self.citing_impact_lbl.set_visible(False)
        return False

    def _apply_citing_impact(self, result, is_stale):
        if not result:
            return False
        bits = []
        for kind in ("software", "method", "idea"):
            b = result.get(kind) or {}
            total = b.get("total") or 0
            n_works = b.get("n_works") or 0
            if n_works == 0:
                continue
            bits.append("{} {}".format(kind, _fmt_compact(total)))
        if not bits:
            self.citing_impact_lbl.set_visible(False)
            return False
        stale_marker = " (stale)" if is_stale else ""
        self.citing_impact_lbl.set_markup(
            "<span size='small' alpha='65%'>"
            "Citing-impact: {}{}</span>".format(
                GLib.markup_escape_text("  ·  ".join(bits)),
                GLib.markup_escape_text(stale_marker)))
        # Build a tooltip with the per-bucket breakdown so the
        # compact chip stays compact but the detail is one hover
        # away.
        tip_lines = ["Citations of papers that cite this author's "
                     "works (self-cites excluded), bucketed by the "
                     "kind of work being cited."]
        for kind in ("software", "method", "idea"):
            b = result.get(kind) or {}
            n_works = b.get("n_works") or 0
            if n_works == 0:
                continue
            total = b.get("total") or 0
            n_citing = b.get("n_citing") or 0
            mean = (total / n_citing) if n_citing else 0.0
            tip_lines.append(
                "{}: {} works, {} citing papers, "
                "{} total cites of citers, mean {:.1f}".format(
                    kind.capitalize(),
                    n_works,
                    "{:,}".format(n_citing),
                    "{:,}".format(total),
                    mean))
        when = result.get("computed_at")
        if when:
            tip_lines.append("Computed {}.".format(when))
        self.citing_impact_lbl.set_tooltip_text("\n".join(tip_lines))
        self.citing_impact_lbl.set_visible(True)
        return False

    # --- Sort toggle ---------------------------------------------------

    def _on_refresh_works(self, _btn):
        """Drop the cache for this author and re-fetch the current
        sort. The other sort's cache is dropped too, so flipping
        the toggle after a refresh also goes to OpenAlex."""
        oa_id = self.authorship.get("openalex_id")
        if oa_id:
            try:
                index.clear_author_works_cache(self.conn, oa_id)
            except Exception as e:
                print("[author_works] cache clear failed:", e)
        self.list_box_clear()
        self.status.set_markup(
            "<span alpha='75%'>Refreshing from OpenAlex…</span>")
        self._spawn_works_only_fetch()

    def _on_sort_toggled(self, btn, sort_key):
        # Only react to the *activation* event — the deactivated peer
        # also fires "toggled".
        if not btn.get_active():
            return
        if sort_key == self._works_sort:
            return
        self._works_sort = sort_key
        # Re-fetch with the new sort. Show a placeholder while we wait.
        self.list_box_clear()
        self.status.set_markup(
            "<span alpha='75%'>Re-sorting from OpenAlex…</span>")
        self._spawn_works_only_fetch()

    def list_box_clear(self):
        child = self.list_box.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.list_box.remove(child)
            child = nxt
        # The rows are gone; anything find remembered about them is
        # now a reference to a removed widget.
        self._find_rows = []
        self._find_matches = []
        self._find_at = -1

    def _spawn_works_only_fetch(self):
        """Background refresh of just the works list (skip the
        author-profile and coauthors calls — those don't change with
        the sort choice)."""
        orcid = self.authorship.get("orcid")
        oa_id = self.authorship.get("openalex_id")
        threading.Thread(
            target=self._do_works_only_fetch, args=(orcid, oa_id),
            daemon=True).start()

    def _do_works_only_fetch(self, orcid, oa_id):
        works = self._cached_or_fetch_works(orcid, oa_id, self._works_sort)
        GLib.idle_add(self._apply_works_only, works)

    def refresh_in_library(self):
        """Recompute which listed works are now in the library and
        rebuild the rows in place. Called by the parent BrowserWindow
        after an import lands elsewhere (e.g. the browser extension)
        while this window is open, so the '✓ in library' badge and the
        Add-to-Archive button update without reopening the dialog.

        Self-contained: works are recovered from the per-row stash set
        in _make_work_row, so this doesn't depend on the fetch path.
        A no-op when membership is unchanged, to avoid needless
        rebuilds on unrelated reloads."""
        try:
            new_existing = _existing_dois(self.conn)
        except Exception:
            return
        if new_existing == self._existing:
            return
        self._existing = new_existing
        works = []
        child = self.list_box.get_first_child()
        while child is not None:
            w = getattr(child, "_work", None)
            if w is not None:
                works.append(w)
            child = child.get_next_sibling()
        if not works:
            return
        self.list_box_clear()
        for w in works:
            self.list_box.append(self._make_work_row(w))

    def _fill_funding(self):
        """Populate the funders section from the local index.

        Synchronous: it is one query over the catalogue and some JSON
        parsing — a few milliseconds on a 200-paper library — so it
        does not need the thread-and-idle_add dance the networked
        sections use."""
        box = getattr(self, "funding_box", None)
        if box is None:
            return
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)
        try:
            rows = funding.profile(
                funding.papers_from_index(self.conn),
                name=self.authorship.get("name"),
                openalex_id=self.authorship.get("openalex_id"),
                orcid=self.authorship.get("orcid"))
        except Exception as e:
            print("funding: could not build profile:", e)
            rows = []
        if not rows:
            self.funding_label.set_visible(False)
            return
        for entry in rows:
            row = Gtk.Label(xalign=0.0)
            row.set_markup(
                "{}  <span size='small' alpha='65%'>{}</span>".format(
                    GLib.markup_escape_text(entry["funder"]),
                    GLib.markup_escape_text(funding.summarise(entry))))
            row.set_wrap(True)
            row.set_max_width_chars(52)
            row.set_margin_start(4)
            if entry["awards"]:
                row.set_tooltip_text(
                    "Award numbers as deposited by the publisher: {}\n"
                    "The funder each is filed under is sometimes wrong "
                    "on papers with many funders.".format(
                        ", ".join(entry["awards"])))
            box.append(row)
        _set_section_count(self.funding_label,
                           "Funders on the papers you have", len(rows))
        self.funding_label.set_visible(True)

    def _do_gtr(self):
        """Look this author up in Gateway to Research, cache, show.

        Cached by trail key for 30 days, misses included: most
        authors are not UK-funded, and re-asking GtR on every page
        open would be a request per open to learn nothing again."""
        key = index.author_trail_key(self.authorship)
        if not key:
            return
        conn = None
        try:
            conn = index.connect_existing(index.db_path_of(self.conn))
        except Exception:
            conn = None
        payload = None
        if conn is not None:
            cached = index.get_author_funding(conn, key)
            if cached and index.author_funding_fresh(cached):
                payload = cached["payload"]
        if payload is None:
            try:
                payload = gtr.grants_for_author(self.authorship)
            except Exception as e:
                print("gtr: lookup failed:", e)
                payload = None
            if payload is not None and conn is not None:
                try:
                    index.set_author_funding(conn, key, payload)
                except Exception:
                    pass
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        if payload:
            GLib.idle_add(self._apply_gtr, payload)

    def _apply_gtr(self, payload):
        box = getattr(self, "gtr_box", None)
        if box is None:
            return False
        while True:
            child = box.get_first_child()
            if child is None:
                break
            box.remove(child)
        grants = (payload or {}).get("grants") or []
        if not payload.get("matched") or not grants:
            self.gtr_label.set_visible(False)
            return False
        for g in grants:
            row = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
            row.set_margin_start(4)
            title = Gtk.Label(xalign=0.0)
            role = "PI" if g.get("is_pi") else "Co-I"
            title.set_markup(
                "<span size='small' weight='bold'>{}</span>  {}".format(
                    role, GLib.markup_escape_text(g.get("title") or "—")))
            title.set_wrap(True)
            title.set_max_width_chars(52)
            row.append(title)
            detail = Gtk.Label(xalign=0.0)
            detail.set_markup(
                "<span size='small' alpha='65%'>{}</span>".format(
                    GLib.markup_escape_text(gtr.summarise_grant(g))))
            detail.set_wrap(True)
            detail.set_max_width_chars(52)
            row.append(detail)
            box.append(row)
        total = gtr.total_awarded(grants)
        if total:
            foot = Gtk.Label(xalign=0.0)
            n_pi = sum(1 for g in grants if g.get("is_pi"))
            foot.set_markup(
                "<span size='small' alpha='75%'>£{:,} across {} grant{} "
                "as PI</span>".format(total, n_pi, "" if n_pi == 1 else "s"))
            foot.set_margin_start(4)
            foot.set_margin_top(4)
            box.append(foot)
        if payload.get("confidence") == "name":
            note = Gtk.Label(xalign=0.0)
            note.set_markup(
                "<span size='small' alpha='55%'>matched by name, not "
                "ORCID</span>")
            note.set_margin_start(4)
            box.append(note)
        _set_section_count(self.gtr_label, "UKRI grants", len(grants))
        self.gtr_label.set_visible(True)
        return False

    def _collaborator_roles(self, works):
        """PI/Group verdicts for the frequent-collaborator chips.
        Evidence: the works just fetched for the current sort plus
        whatever the cache holds for both sorts — a previous
        session's "most cited" fetch can carry decades-old shared
        papers the recent-50 misses. Any failure means no
        cartouches, never a broken page."""
        try:
            target = self.authorship.get("openalex_id")
            if not target:
                return {}
            lists = [works or []]
            for sort_key in ("recent", "cited"):
                try:
                    cached = index.get_author_works_cache(
                        self.conn, target, sort_key)
                except Exception:
                    cached = None
                if cached:
                    lists.append(cached.get("works") or [])
            return metrics.infer_collaborator_roles(target, lists)
        except Exception as e:
            print("[author_works] role inference failed:", e)
            return {}

    def _rebuild_coauth_chips(self, works):
        """(Re)populate the collaborator FlowBox, attaching a PI /
        Group cartouche where the shared-works evidence supports
        one. Called from _apply_results and again on sort toggles,
        when fresh works may deepen the evidence."""
        if not self._coauths:
            return
        roles = self._collaborator_roles(works)
        while (child := self.coauth_box.get_first_child()) is not None:
            self.coauth_box.remove(child)
        me = self.authorship.get("name") or "this author"
        for c in self._coauths:
            btn = Gtk.Button()
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=6)
            lbl = Gtk.Label()
            lbl.set_label("{}  ({})".format(c["name"], c["count"]))
            row.append(lbl)
            info = roles.get(c.get("openalex_id"))
            if info:
                chip = Gtk.Label()
                if info["role"] == "pi":
                    color, text = "#1c71d8", "PI"
                    tip = ("Corresponding or last author on {} shared "
                           "paper(s) while both at {} — likely the "
                           "PI.").format(
                               info["votes"], info["institution"])
                else:
                    color, text = "#26a269", "Group"
                    tip = ("{} was corresponding or last author on {} "
                           "shared paper(s) while both at {} — {} "
                           "likely worked in their group.").format(
                               me, info["votes"], info["institution"],
                               c["name"])
                if info["votes"] <= 1:
                    # Single-vote verdicts are thin evidence — grey
                    # the cartouche rather than suppress it. alpha
                    # (not a fixed grey) tracks light/dark themes.
                    chip.set_markup(
                        "<span size='x-small' weight='bold' "
                        "alpha='55%'>{}</span>".format(text))
                else:
                    chip.set_markup(
                        "<span size='x-small' weight='bold' "
                        "foreground='{}'>{}</span>".format(color, text))
                chip.set_tooltip_text(tip)
                row.append(chip)
            btn.set_child(row)
            btn.add_css_class("flat")
            btn.set_tooltip_text(
                "Open papers by {}".format(c["name"]))
            btn.connect(
                "clicked",
                lambda _b, c=c: self._open_coauthor(c))
            self.coauth_box.append(btn)

    # --- Who cites this author ----------------------------------------

    def _start_citers_load(self):
        """Fill the "Cited most often by" row, from cache when it is
        fresh and from OpenAlex otherwise. Always off the main thread:
        the fetch is two HTTP calls, and this row is not what the
        reader opened the page for."""
        oid = (self.authorship or {}).get("openalex_id")
        if not oid:
            return
        cached = index.get_author_relations(self.conn, oid)
        if cached is not None:
            self._apply_citers(cached["cited_by_top"])
            if index.author_relations_fresh(cached):
                return                      # nothing more to do
        threading.Thread(target=self._citers_worker, args=(oid,),
                         daemon=True).start()

    def _on_citers_progress(self, message):
        GLib.idle_add(_set_section_note, self.citers_label,
                      "Cited most often by", message)

    def _citers_worker(self, oid):
        try:
            top = metrics.fetch_top_citing_authors(
                oid, on_progress=self._on_citers_progress)
        except Exception:
            top = None
        if top is None:
            return                          # leave any cached list up
        try:
            path = index.db_path_of(self.conn)
            if path:
                conn = index.connect_existing(path)
                try:
                    index.set_author_relations(conn, oid, top)
                finally:
                    conn.close()
        except Exception:
            pass                            # the display still works
        GLib.idle_add(self._apply_citers, top)

    def _apply_citers(self, top):
        while (child := self.citers_box.get_first_child()) is not None:
            self.citers_box.remove(child)
        if not top:
            self.citers_label.set_visible(False)
            return False
        for a in top:
            btn = Gtk.Button()
            lbl = Gtk.Label()
            lbl.set_label("{}  ({})".format(a["name"], a["count"]))
            btn.set_child(lbl)
            btn.add_css_class("flat")
            btn.set_tooltip_text(
                "{} papers by {} cite {}".format(
                    a["count"], a["name"],
                    self.authorship.get("name") or "this author"))
            btn.connect("clicked", lambda _b, a=a: self._open_citer(a))
            self.citers_box.append(btn)
        _set_section_count(self.citers_label,
                           "Cited most often by", len(top))
        self.citers_label.set_visible(True)
        return False

    def _open_citer(self, a):
        """Open the citing author's own page — the same route a
        collaborator chip takes."""
        self._open_coauthor({"openalex_id": a["openalex_id"],
                             "name": a["name"]})

    def _apply_works_only(self, works):
        if not works:
            self.status.set_markup(self._empty_status_markup())
            return False
        self.status.set_markup(self._works_status_markup(len(works)))
        for w in works:
            self.list_box.append(self._make_work_row(w))
        # Fresh works may add evidence (e.g. first "most cited"
        # fetch) — refresh the collaborator cartouches.
        self._rebuild_coauth_chips(works)
        return False

    def _cache_first_publication_year(self, counts_by_year):
        """Store the earliest year this author published, from the
        profile's per-year counts.

        The earliest year with a non-zero `works_count`, not simply the
        first entry: OpenAlex can open the series on a year that only
        carries citations of earlier work. Measured across real trail
        rows — Murshudov 1989 (36 entries), Dialpuri 2022 (5),
        Sheldrick 1934 (60) — which also puts paid to the claim that
        the series is capped at ten years."""
        years = [r.get("year") for r in (counts_by_year or [])
                 if r.get("works_count") and r.get("year")]
        if not years:
            return
        key = index.author_trail_key(self.authorship)
        if key:
            index.set_author_first_publication_year(
                self.conn, key, min(years))

    def _empty_status_markup(self):
        """Pick the right "nothing to show" message. When OpenAlex is
        refusing us, the empty result is a symptom of that rather than
        "the author really has no works" — and `openalex_blocked_reason`
        knows whether the remedy is waiting (quota) or pasting a key
        (no authentication), which are not interchangeable."""
        reason = metrics.openalex_blocked_reason()
        if reason:
            return ("<span foreground='#cc6633'>Search blocked — "
                    "{}</span>".format(safe_pango_markup(reason)))
        return "<span alpha='75%'>No works found.</span>"

    def _works_status_markup(self, n):
        sort_label = ("most recent" if self._works_sort == "recent"
                      else "most cited")
        return "<span alpha='75%'>{} {} works</span>".format(n, sort_label)

    def _apply_results(self, profile, works, coauths=None):
        # Whatever the outcome, the waiting is over.
        self._stop_load_pulse()
        # OpenAlex circuit breaker tripped → profile is None and
        # works is []. Surface the rate-limit reason rather than
        # leaving the "Loading…" line stuck and saying "No works
        # found" on the empty body.
        reason = metrics.openalex_blocked_reason()
        if not profile and not works and reason:
            blocked = ("<span size='small' foreground='#cc6633'>"
                       "Search blocked — {}</span>".format(
                           safe_pango_markup(reason)))
            self.stats_lbl.set_markup(blocked)
            self.status.set_markup(blocked)
            return
        if profile:
            # The per-work authorship dict often lacks an ORCID (OpenAlex
            # only carries it when the publisher deposited one for that
            # specific paper). The author *record* we just fetched
            # usually has it — so backfill the ORCID line when the
            # authorship didn't supply one.
            if not self.authorship.get("orcid") and profile.get("orcid"):
                self.authorship["orcid"] = profile["orcid"]
                self._sub_orcid_lbl.set_markup(
                    "<span size='small' alpha='75%'>ORCID {}</span>".format(
                        GLib.markup_escape_text(profile["orcid"])))
                self._sub_orcid_lbl.set_visible(True)
            bits = []
            if profile.get("works_count"):
                bits.append("{} works".format(profile["works_count"]))
            if profile.get("cited_by_count"):
                bits.append("{} citations".format(profile["cited_by_count"]))
            if profile.get("h_index") is not None:
                bits.append("h-index {}".format(profile["h_index"]))
            if bits:
                self.stats_lbl.set_markup(
                    "<span size='small' alpha='75%'>{}</span>".format(
                        GLib.markup_escape_text("  ·  ".join(bits))))
            cby = profile.get("counts_by_year") or []
            # Cache the career start for the sidebar's "First
            # publication" sort. Free here — the sparkline needs
            # `counts_by_year` anyway — and the alternative is one
            # profile fetch per author before the sidebar can draw.
            self._cache_first_publication_year(cby)
            peak = max((r["cited_by_count"] for r in cby), default=0)
            if peak >= HISTOGRAM_MIN_PEAK:
                self._hist_data = cby
                self.hist_area.set_visible(True)
                self.hist_area.queue_draw()

            self._populate_affiliations(profile.get("affiliations") or [])

        if coauths:
            self._coauths = coauths
            _set_section_count(self.coauth_label,
                               "Frequent collaborators",
                               len(self._coauths or []))
            self.coauth_label.set_visible(True)
            self._rebuild_coauth_chips(works)

        if not works:
            self.status.set_markup(self._empty_status_markup())
            return
        self.status.set_markup(self._works_status_markup(len(works)))
        for w in works:
            self.list_box.append(self._make_work_row(w))
        return False

    # --- Affiliations -------------------------------------------------

    def _populate_affiliations(self, rows):
        """Set the header's "current institution" label to the most
        recent affiliation, and stash the full list behind a popover
        on `self._aff_history_btn`. Hides the button if the list is
        empty or the author has only one institution (no history to
        show)."""
        if not rows:
            return
        current = current_affiliation(rows)
        if current is None:
            return
        # Remember the profile's current affiliation — the web-photo
        # search uses it to disambiguate common names even when the
        # opening authorship carried no institution.
        self._current_institution = current.get("display_name")
        if self._on_institution is not None and current.get("display_name"):
            self._on_institution(current["display_name"])
        # Always overwrite, never only-when-empty. The label was
        # seeded from `authorship["institution"]`, which is the
        # address on whichever paper the user opened this author
        # from — open a 2006 paper and you get their 2006 employer.
        # OpenAlex's most-recent affiliation is the better answer,
        # and the sidebar row was already being corrected this way,
        # so guarding here just made the two disagree on screen.
        if current.get("display_name"):
            self._sub_inst_lbl.set_markup(
                _INSTITUTION_MARKUP.format(
                    GLib.markup_escape_text(current["display_name"])))
            self._sub_inst_lbl.set_visible(True)

        if len(rows) <= 1:
            return  # nothing extra to surface
        # Build the popover contents — a vertically scrollable list
        # of "year_range  institution_name" lines.
        pop = Gtk.Popover()
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        outer.set_margin_start(10)
        outer.set_margin_end(10)
        outer.set_margin_top(8)
        outer.set_margin_bottom(8)
        hdr = Gtk.Label(xalign=0.0)
        hdr.set_markup(
            "<b>Prior institutions</b>  "
            "<span alpha='65%' size='small'>({})</span>".format(len(rows)))
        outer.append(hdr)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_min_content_height(min(360, 26 * len(rows) + 10))
        scrolled.set_min_content_width(420)
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        for r in rows:
            body.append(self._make_aff_row(r))
        scrolled.set_child(body)
        outer.append(scrolled)
        pop.set_child(outer)
        # Icon + "+N" count as the button face: the count hints at
        # what is behind the popover (N institutions beyond the
        # current one) and makes the widget read as a real button.
        face = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        face.append(
            Gtk.Image.new_from_icon_name("document-open-recent-symbolic"))
        count_lbl = Gtk.Label()
        count_lbl.set_markup(
            "<span size='small'>+{}</span>".format(len(rows) - 1))
        face.append(count_lbl)
        self._aff_history_btn.set_child(face)
        self._aff_history_btn.set_popover(pop)
        self._aff_history_btn.set_visible(True)

    def _make_aff_row(self, r):
        ymin, ymax = r["year_min"], r["year_max"]
        years = "{}".format(ymin) if ymin == ymax else "{}–{}".format(ymin, ymax)
        lbl = Gtk.Label(xalign=0.0)
        lbl.set_wrap(True)
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        lbl.set_max_width_chars(80)
        lbl.set_markup(
            "<span size='small'>"
            "<tt>{:>9}</tt>  <span alpha='80%'>{}</span>"
            "</span>".format(
                years, GLib.markup_escape_text(r["display_name"])))
        return lbl

    # --- Author photo -------------------------------------------------

    def _set_avatar_from_disk(self):
        """Show the stored photo, or the initials/silhouette disc
        when there is none (or the file is unreadable — corrupt
        files degrade to the placeholder, never an error)."""
        path = author_image.image_path(self.authorship)
        texture = None
        if path and os.path.isfile(path):
            try:
                texture = Gdk.Texture.new_from_file(
                    Gio.File.new_for_path(path))
            except Exception:
                texture = None
        self.avatar.set_custom_image(texture)

    def _build_avatar_menu(self):
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        has_key = author_image.image_path(self.authorship) is not None

        fetch_btn = Gtk.Button(label="Fetch from Wikidata")
        fetch_btn.add_css_class("flat")
        fetch_btn.connect("clicked", self._on_fetch_wikidata)
        box.append(fetch_btn)

        search_btn = Gtk.Button(label="Search the web for a photo…")
        search_btn.add_css_class("flat")
        search_btn.connect("clicked", self._on_search_photo)
        box.append(search_btn)

        choose_btn = Gtk.Button(label="Choose image file…")
        choose_btn.add_css_class("flat")
        choose_btn.connect("clicked", self._on_choose_image)
        box.append(choose_btn)

        self._remove_img_btn = Gtk.Button(label="Remove photo")
        self._remove_img_btn.add_css_class("flat")
        self._remove_img_btn.connect("clicked", self._on_remove_image)
        box.append(self._remove_img_btn)

        if not has_key:
            for b in (fetch_btn, search_btn, choose_btn,
                      self._remove_img_btn):
                b.set_sensitive(False)
                b.set_tooltip_text(
                    "No ORCID or OpenAlex ID — photos need a stable "
                    "author identity to be stored under.")
        pop.set_child(box)
        pop.connect("show", lambda _p: self._remove_img_btn.set_visible(
            (author_image.image_path(self.authorship) or "") != ""
            and os.path.isfile(author_image.image_path(self.authorship))))
        return pop

    def _on_search_photo(self, _btn):
        """Open a DuckDuckGo image search for this author in the
        preferred browser. The user drags their pick back onto the
        avatar — the drop target's URL path downloads and stores it,
        so the human does the face-recognition instead of a
        classifier."""
        self._avatar_menu.popdown()
        name = self.authorship.get("name") or ""
        inst = (self.authorship.get("institution")
                or getattr(self, "_current_institution", None))
        query = "{}, {}".format(name, inst) if inst else name
        _open_url("https://duckduckgo.com/?q={}&iax=images&ia=images"
                  .format(urllib.parse.quote_plus(query)))
        self._avatar_status(
            "Browser opened — drag your chosen image onto the avatar.")

    def _avatar_status(self, text):
        if not text:
            self._avatar_status_lbl.set_visible(False)
            return
        self._avatar_status_lbl.set_markup(
            "<span size='small' alpha='75%'>{}</span>".format(
                GLib.markup_escape_text(text)))
        self._avatar_status_lbl.set_visible(True)

    def _apply_new_image(self, path):
        """Main-thread: refresh the header avatar and tell the
        hosting window (sidebar row) about the change."""
        # The photo arrived — any "drag your chosen image…" /
        # "Looking up…" instruction is done with.
        self._avatar_status("")
        self._set_avatar_from_disk()
        if self._on_image_changed is not None:
            self._on_image_changed(path)
        return False

    def _on_fetch_wikidata(self, _btn):
        self._avatar_menu.popdown()
        self._avatar_status("Looking up Wikidata portrait…")

        def work():
            try:
                path = author_image.fetch_wikidata_portrait(
                    self.authorship)
            except Exception as e:
                GLib.idle_add(self._avatar_status,
                              "Wikidata lookup failed: {}".format(e))
                return
            if path:
                GLib.idle_add(self._apply_new_image, path)
                GLib.idle_add(self._avatar_status, "Portrait fetched.")
            else:
                GLib.idle_add(
                    self._avatar_status,
                    "No Wikidata portrait for this author — drop an "
                    "image on the avatar or choose a file.")
        threading.Thread(target=work, daemon=True).start()

    def _on_choose_image(self, _btn):
        self._avatar_menu.popdown()
        dlg = Gtk.FileDialog()
        f = Gtk.FileFilter()
        f.set_name("Images")
        f.add_mime_type("image/*")
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(f)
        dlg.set_filters(filters)

        def done(d, result):
            try:
                gfile = d.open_finish(result)
            except GLib.Error:
                return   # cancelled
            try:
                path = author_image.save_image(
                    self.authorship, gfile.get_path())
            except Exception as e:
                self._avatar_status("Couldn't use that image: {}"
                                    .format(e))
                return
            self._apply_new_image(path)
        dlg.open(self.get_root(), None, done)

    def _on_remove_image(self, _btn):
        self._avatar_menu.popdown()
        author_image.remove_image(self.authorship)
        self._apply_new_image(None)

    def _save_avatar_from_url(self, url):
        # Downloads happen off the UI thread; the download-worker
        # results are applied back on the main loop via idle_add,
        # matching this file's other background-fetch patterns.
        def work():
            try:
                # download_image resolves og:image when the drop is a
                # web page rather than the image itself (dragging
                # Wikipedia's infobox portrait delivers the File:
                # description page).
                data = author_image.download_image(url)
                path = author_image.save_image(
                    self.authorship, data)
            except Exception as e:
                GLib.idle_add(self._avatar_status,
                              "Couldn't fetch that image: {}"
                              .format(e))
                return
            GLib.idle_add(self._apply_new_image, path)
        threading.Thread(target=work, daemon=True).start()

    def _on_avatar_drop(self, _target, value, _x, _y):
        if isinstance(value, Gdk.FileList):
            files = value.get_files()
            if not files:
                return False
            path = files[0].get_path()
            if path is None:
                # Browsers advertise dragged images/links as
                # text/uri-list, which GDK deserializes to
                # Gdk.FileList (the preferred gtype ahead of str
                # in set_gtypes) rather than a plain string. The
                # resulting GFile wraps a remote https: URI with
                # no local path, so it needs the same download
                # path as the str branch below.
                uri = files[0].get_uri()
                if uri and uri.lower().startswith(("http://", "https://")):
                    self._save_avatar_from_url(uri)
                    return True
                self._avatar_status("Couldn't read that drop")
                return False
            try:
                path = author_image.save_image(self.authorship, path)
            except Exception as e:
                self._avatar_status("Couldn't use that image: {}"
                                    .format(e))
                return False
            self._apply_new_image(path)
            return True
        if isinstance(value, str):
            url = value.strip()
            if not url.lower().startswith(("http://", "https://")):
                return False
            self._save_avatar_from_url(url)
            return True
        return False

    def _open_coauthor(self, c):
        authorship = {
            "name": c["name"],
            "openalex_id": c["openalex_id"],
            "orcid": None,
            "institution": None,
        }
        open_window(self.get_root(), self.conn, authorship)

    # --- Drawing -------------------------------------------------------

    def _on_draw_hist(self, area, cr, width, height):
        _draw_histogram(area, cr, width, height, self._hist_data)

    # --- Per-work row --------------------------------------------------

    def _make_work_row(self, w):
        frame = Gtk.Frame()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        box.set_margin_start(8)
        box.set_margin_end(8)
        box.set_margin_top(6)
        box.set_margin_bottom(6)

        # Title row (with optional "in library" badge).
        title_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        title = w.get("title") or "(untitled)"
        title_lbl = Gtk.Label(xalign=0.0)
        title_lbl.set_wrap(True)
        title_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        title_lbl.set_selectable(True)
        title_lbl.set_hexpand(True)
        from .browse import _title_color
        colour = _title_color(self)

        def _wrap_title(marked, _c=colour):
            return "<span foreground='{}'><b>{}</b></span>".format(
                _c, marked)
        title_lbl.set_markup(_wrap_title(safe_pango_markup(title)))
        title_row.append(title_lbl)
        fields = [(title_lbl, title, _wrap_title)]

        # Retracted chip — highest-priority warning, drawn first
        # so it sits closest to the title. OpenAlex carries the
        # flag directly on the work, so this is free.
        if w.get("is_retracted"):
            rl = Gtk.Label()
            rl.set_markup(
                "<span size='small' foreground='#cc3333'>"
                "<b>⚠ RETRACTED</b></span>")
            rl.set_valign(Gtk.Align.START)
            rl.set_tooltip_text(
                "OpenAlex flags this work as retracted.")
            title_row.append(rl)

        # Paratext chip — editorials, tables of contents, masthead
        # entries. Muted so it doesn't shout for attention; it's
        # mostly a "this isn't a real paper" hint.
        if w.get("is_paratext"):
            pl = Gtk.Label()
            pl.set_markup(
                "<span size='small' foreground='#888888'>"
                "<b>paratext</b></span>")
            pl.set_valign(Gtk.Align.START)
            pl.set_tooltip_text(
                "Editorial / table-of-contents / masthead entry "
                "rather than a research article.")
            title_row.append(pl)

        # Preprint badge — OpenAlex work type, or a DOI on a known
        # preprint server (bioRxiv / arXiv / chemRxiv / …). Same
        # "PRE" styling as the library card's preprint chip.
        if (w.get("type") == "preprint"
                or metrics.is_preprint_doi(w.get("doi"))):
            pre = Gtk.Label()
            pre.set_markup(
                "<span size='small' foreground='#cc6600'>"
                "<b>PRE</b></span>")
            pre.set_valign(Gtk.Align.START)
            pre.set_tooltip_text("Preprint")
            # Prepend so PRE leads the title ("PRE  Title"), matching
            # the main library card rather than trailing after it.
            title_row.prepend(pre)

        # Author-response chip — the peer-review response companion to
        # a paper (OpenAlex titles these "Author Response: ..." /
        # "Author response for ..."). Prepended like PRE so it leads
        # the title.
        if "author response" in title.lower():
            ar = Gtk.Label()
            ar.set_markup(
                "<span size='small' foreground='#4d9f96'>"
                "<b>AR</b></span>")
            ar.set_valign(Gtk.Align.START)
            ar.set_tooltip_text("Author response (peer-review companion)")
            title_row.prepend(ar)

        # Venue chip (Zenodo / JoVE / bioRxiv / arXiv / ...). One
        # at most. Sits between the title and the in-library
        # badge, right-aligned visually because the title's
        # hexpand pushes it.
        chip = _venue_chip(w)
        if chip:
            label, fg = chip
            chip_lbl = Gtk.Label()
            chip_lbl.set_markup(
                "<span size='small' foreground='{}'>"
                "<b>{}</b></span>".format(
                    fg, GLib.markup_escape_text(label)))
            chip_lbl.set_valign(Gtk.Align.START)
            title_row.append(chip_lbl)

        # License / OA chip. Same chip factories as the main card
        # in browse.py — lazy import to dodge the circular
        # browse↔author_works dependency. Free data (already in the
        # OpenAlex response we just consumed).
        from .browse import make_license_chip, make_oa_chip
        lic_chip = make_license_chip(w.get("license_label"), None)
        if lic_chip is not None:
            lic_chip.set_valign(Gtk.Align.START)
            title_row.append(lic_chip)
        else:
            oa_chip = make_oa_chip(w.get("is_oa"), w.get("oa_status"))
            if oa_chip is not None:
                oa_chip.set_valign(Gtk.Align.START)
                title_row.append(oa_chip)

        doi = w.get("doi")
        in_library = bool(doi and doi.lower() in self._existing)
        if in_library:
            badge = Gtk.Label()
            badge.set_markup(
                "<span size='small' foreground='#33aa33'>"
                "<b>✓ in library</b></span>")
            badge.set_valign(Gtk.Align.START)
            title_row.append(badge)
        box.append(title_row)

        # Authors. Fill the line to the character budget; when names
        # were dropped, hovering shows the complete list.
        if w.get("authors"):
            auth_line = _truncate_authors(w["authors"])
            auth_lbl = Gtk.Label(xalign=0.0)
            auth_lbl.set_wrap(True)
            auth_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            def _wrap_authors(marked):
                return "<span size='small'>{}</span>".format(marked)
            auth_lbl.set_markup(
                _wrap_authors(GLib.markup_escape_text(auth_line)))
            fields.append((auth_lbl, auth_line, _wrap_authors))
            if auth_line.endswith("et al."):
                auth_lbl.set_tooltip_text(", ".join(w["authors"]))
            box.append(auth_lbl)

        # Funders. Up to two displayed; rest collapse to "+N more"
        # so the row doesn't grow unbounded for heavily-funded
        # consortium papers. Award IDs go into the tooltip.
        grants = w.get("grants") or []
        if grants:
            visible = grants[:2]
            extra = len(grants) - len(visible)
            funder_bits = [g["funder"] for g in visible]
            text = "Funded by " + ", ".join(funder_bits)
            if extra > 0:
                text += " · +{} more".format(extra)
            tip_parts = []
            for g in grants:
                if g.get("award_id"):
                    tip_parts.append("{} ({})".format(g["funder"],
                                                       g["award_id"]))
                else:
                    tip_parts.append(g["funder"])
            funder_lbl = Gtk.Label(xalign=0.0)
            funder_lbl.set_wrap(True)
            funder_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            funder_lbl.set_markup(
                "<span size='small' alpha='75%'>{}</span>".format(
                    GLib.markup_escape_text(text)))
            funder_lbl.set_tooltip_text("\n".join(tip_parts))
            box.append(funder_lbl)

        # Year · Journal · Type · Citations · FWCI · Topic.
        meta_bits = []
        if w.get("publication_date"):
            meta_bits.append(w["publication_date"])
        elif w.get("year"):
            meta_bits.append(str(w["year"]))
        if w.get("journal"):
            meta_bits.append(w["journal"])
        if w.get("type") and w["type"] != "article":
            meta_bits.append(w["type"])
        if w.get("citations"):
            meta_bits.append("cited {}×".format(w["citations"]))
        # FWCI — Field-Weighted Citation Impact. >1 means above the
        # field average; we render with 2 dp because the value is
        # usually 0.05–10ish. Skip when missing or zero (zero often
        # means "too new to score" rather than "actually zero").
        fwci = w.get("fwci")
        if isinstance(fwci, (int, float)) and fwci > 0:
            meta_bits.append("FWCI {:.2f}".format(fwci))
        if w.get("top_topic"):
            meta_bits.append(w["top_topic"])
        if meta_bits:
            meta_text = "  ·  ".join(meta_bits)
            meta_lbl = Gtk.Label(xalign=0.0)

            def _wrap_meta(marked):
                return "<span size='small' alpha='75%'>{}</span>".format(
                    marked)
            meta_lbl.set_markup(
                _wrap_meta(GLib.markup_escape_text(meta_text)))
            meta_lbl.set_wrap(True)
            meta_lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            box.append(meta_lbl)
            # Year, journal and topic are on the line, so they are
            # findable. The abstract is fetched but not shown, and
            # matching text the reader cannot see reads as a false
            # positive — so it is left out.
            fields.append((meta_lbl, meta_text, _wrap_meta))

        # Action buttons.
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_row.set_margin_top(2)
        if doi:
            b = Gtk.Button(label="DOI")
            b.set_tooltip_text("https://doi.org/" + doi)
            b.connect(
                "clicked",
                lambda _b, d=doi: _open_url("https://doi.org/" + d))
            btn_row.append(b)
        oa_id = w.get("openalex_id")
        if oa_id:
            b = Gtk.Button(label="OpenAlex")
            b.connect(
                "clicked",
                lambda _b, i=oa_id: _open_url("https://openalex.org/" + i))
            btn_row.append(b)
        # OpenAlex disambiguates: pdf_url is the primary direct PDF
        # (best OA location), pdf_urls is *all* known OA mirrors
        # (PMC / repositories / preprint servers), landing_url is the
        # publisher's HTML article page.
        pdf_url = w.get("pdf_url")
        pdf_urls = list(w.get("pdf_urls") or ([pdf_url] if pdf_url else []))
        landing_url = w.get("landing_url") or w.get("oa_url")
        view_target = pdf_url or landing_url
        if w.get("is_oa") and view_target:
            b = Gtk.Button(label="View")
            b.set_tooltip_text(view_target)
            b.connect(
                "clicked",
                lambda _b, u=view_target: _open_url(u))
            _attach_copy_link_menu(b, view_target)
            btn_row.append(b)

        # In-library rows get an Open button whatever their OA
        # status — the library copy is right there. A ghost row
        # (metadata-only BibTeX import) has the DOI but no PDF, so
        # it keeps an inert label.
        already_in_lib = (doi or "").lower() in self._existing
        if already_in_lib:
            lib_btn = Gtk.Button()
            local = _library_pdf_for_doi(self.conn, doi)
            if local:
                lib_btn.set_label("Open")
                lib_btn.set_tooltip_text(
                    "Open the PDF from your library\n" + local[0])
                lib_btn.connect(
                    "clicked",
                    lambda _b, p=local:
                        viewer.open_viewer(self.get_root(), p[0], p[1]))
            else:
                lib_btn.set_label("In library")
                lib_btn.set_sensitive(False)
                lib_btn.set_tooltip_text(
                    "In the library as metadata only — no PDF "
                    "on disk yet.")
            btn_row.append(lib_btn)
        elif w.get("is_oa") and view_target:
            add_btn = Gtk.Button(label="Add to Archive")
            add_btn.add_css_class("suggested-action")
            if not pdf_urls:
                add_btn.set_sensitive(False)
                add_btn.remove_css_class("suggested-action")
                add_btn.set_tooltip_text(
                    "No direct PDF link from OpenAlex (only a publisher "
                    "landing page is available). Use 'View' to open "
                    "it in a browser, then drag the PDF into Alexandria.")
            else:
                tip_lines = ["Download the open-access PDF into your "
                             "library and extract metadata"]
                if len(pdf_urls) > 1:
                    tip_lines.append(
                        "({} mirror{} available)".format(
                            len(pdf_urls),
                            "" if len(pdf_urls) == 1 else "s"))
                add_btn.set_tooltip_text("\n".join(tip_lines))
                add_btn.connect(
                    "clicked",
                    lambda _b, urls=pdf_urls, d=doi, btn=add_btn:
                        self._on_add_to_archive(urls, d, btn))
            btn_row.append(add_btn)
        if btn_row.get_first_child() is not None:
            box.append(btn_row)

        frame.set_child(box)
        # Stash the work dict on the row so refresh_in_library() can
        # rebuild this row in place when library membership changes
        # (e.g. an import landing via the browser extension).
        frame._work = w
        self._find_rows.append({"row": frame, "fields": fields})
        # A row built while a search is live starts marked, rather than
        # waiting for the next keystroke — rows arrive from a refetch
        # or a sort change with the entry still full.
        query = self._find_entry.get_text() if self._find_entry else ""
        if query:
            self._mark_row(self._find_rows[-1], query)
            self._update_find_summary()
        return frame

    # --- Find in these works -------------------------------------------

    def _on_find_changed(self, entry):
        query = entry.get_text()
        for rec in self._find_rows:
            self._mark_row(rec, query)
        self._find_at = -1
        self._update_find_summary()
        # Land on the first match as soon as there is one, so typing
        # takes you there without a second gesture.
        if self._find_matches:
            self._step_find(1)

    def _mark_row(self, rec, query):
        """Re-render one row's labels with the matches marked, and
        record whether it matched at all."""
        hit = False
        for label, text, wrap in rec["fields"]:
            label.set_markup(wrap(find_text.highlight(text, query)))
            if find_text.spans(text, query):
                hit = True
        rec["match"] = hit

    def _update_find_summary(self):
        query = self._find_entry.get_text()
        self._find_matches = [i for i, rec in enumerate(self._find_rows)
                              if rec.get("match")]
        self._find_count.set_text(
            find_text.summary(len(self._find_matches),
                              len(self._find_rows), query))
        # Stepping buttons only when there is more than one place to
        # step between.
        walkable = len(self._find_matches) > 1
        self._find_prev_btn.set_visible(walkable)
        self._find_next_btn.set_visible(walkable)

    def _step_find(self, delta):
        """Move to the next (or previous) matching row and scroll it
        into view. Wraps around, like a find bar."""
        if not self._find_matches:
            return
        if self._find_at < 0:
            self._find_at = 0 if delta > 0 else len(self._find_matches) - 1
        else:
            self._find_at = ((self._find_at + delta)
                             % len(self._find_matches))
        rec = self._find_rows[self._find_matches[self._find_at]]
        self._scroll_row_into_view(rec["row"])

    def _scroll_row_into_view(self, row):
        """`Gtk.Viewport.scroll_to` needs GTK 4.12; fall back to
        focusing the row, which also brings it in."""
        viewport = self.list_box.get_parent()
        if hasattr(viewport, "scroll_to"):
            try:
                viewport.scroll_to(row, None)
                return
            except Exception:
                pass
        row.grab_focus()

    # ------------------------------------------------------------------
    # Add to Archive: download an OA PDF and import it into the library.
    # ------------------------------------------------------------------

    def _on_add_to_archive(self, urls, doi, btn):
        if not urls:
            return
        # Pick the filename from the first URL (or the DOI fallback).
        fname = _filename_for(doi, urls[0])
        if not fname:
            btn.set_label("No filename")
            btn.set_sensitive(False)
            return

        os.makedirs(LIBRARY_ROOT, exist_ok=True)
        target = os.path.join(LIBRARY_ROOT, fname)

        # Already present? Don't re-download.
        if os.path.exists(target):
            btn.set_label("Already present")
            btn.set_sensitive(False)
            btn.remove_css_class("suggested-action")
            return

        btn.set_sensitive(False)
        btn.set_label("Downloading…")
        # The download narrates itself on the status line; remember
        # what was there so it can be handed back afterwards.
        self._status_before_download = self.status.get_label()
        threading.Thread(
            target=self._do_add_to_archive,
            args=(urls, target, doi, btn),
            daemon=True,
        ).start()

    def _archive_progress(self):
        """A pdf_fetch `on_progress` callback writing to this page's
        status line, holding each milestone long enough to read.

        The button says "Downloading…" and has room for nothing else;
        which host answered, and how many megabytes have arrived, go
        where there is space for them. `_restore_status` puts the
        works summary back afterwards."""
        ticker = status_ticker.StatusTicker(
            show=lambda message: GLib.idle_add(
                self.status.set_markup,
                "<span alpha='75%'>{}</span>".format(
                    GLib.markup_escape_text(message))),
            schedule=lambda delay_ms, fn: GLib.timeout_add(delay_ms, fn))
        return ticker.callback()

    def _restore_status(self):
        """Put back whatever the status line said before the
        download borrowed it — saved verbatim rather than rebuilt,
        because the page does not keep the works list around to
        recount."""
        saved = getattr(self, "_status_before_download", None)
        if saved is not None:
            self.status.set_markup(saved)
        return False

    def _do_add_to_archive(self, urls, target, doi, btn):
        # Try each candidate in order, falling back to the next on
        # failure. Most papers succeed on the first; CF-protected
        # bioRxiv etc. often have a PMC mirror that works.
        progress = self._archive_progress()
        last_msg = ""
        last_url = ""
        for i, url in enumerate(urls):
            if i > 0:
                GLib.idle_add(
                    self._set_add_btn_label, btn,
                    "Trying mirror {} / {}…".format(i + 1, len(urls)))
            ok, msg = pdf_fetch.download_pdf(url, target,
                                             on_progress=progress)
            last_msg = msg
            last_url = url
            if ok:
                break
            print("Add to archive: download failed for {}: {}".format(
                url, msg))
        else:
            # The URLs above come from the work record we already had.
            # When every one of them fails — Cloudflare, a dead
            # mirror — ask the sources that record knows nothing
            # about, the same chain "Get PDF" uses.
            ok = False
            if doi:
                ok, last_url, last_msg = pdf_fetch.fetch_oa_pdf(
                    doi, target, on_progress=progress)
            if not ok:
                n = len(urls)
                tail = (" (tried {} mirror{})".format(
                    n, "" if n == 1 else "s") if n > 1 else "")
                GLib.idle_add(
                    self._add_to_archive_done, btn, False,
                    last_msg + tail, None)
                return

        try:
            # The same status line the download was narrating to: an
            # import can take seconds (poppler twice, up to five
            # network calls), and going quiet between "Downloading…"
            # and the card appearing is what made a slow one look
            # like nothing happening at all.
            GLib.idle_add(self._set_add_btn_label, btn, "Importing…")
            rec, status = importer.import_pdf(self.conn, target,
                                              on_progress=progress)
        except Exception as e:
            print("Add to archive: import failed for {}: {}".format(target, e))
            GLib.idle_add(self._add_to_archive_done, btn, False, str(e), None)
            return
        if doi:
            self._existing.add(doi.lower())
        GLib.idle_add(self._add_to_archive_done, btn, True, status, rec)

    def _set_add_btn_label(self, btn, text):
        btn.set_label(text)
        return False

    def _add_to_archive_done(self, btn, ok, status_or_msg, _rec):
        # Whatever happened, the status line goes back to describing
        # the works list rather than the last download step.
        self._restore_status()
        if ok:
            btn.set_label("Added")
            btn.remove_css_class("suggested-action")
            # Already inactive; leave it that way as a record.
        else:
            btn.set_label("Failed — retry?")
            btn.set_tooltip_text("Last error: " + str(status_or_msg))
            btn.set_sensitive(True)
        return False


class AuthorsWindow(Adw.Window):
    """The one Authors window: a persistent trail of authors down the
    left, the selected author's page on the right — an
    Adw.NavigationSplitView, so each pane gets its own header bar and
    the sidebar folds away on narrow windows. Pages are built
    lazily on first selection and kept alive so switching back is
    instant. The trail itself lives in the `author_trail` table and
    survives restarts; works/impact data comes from the existing
    author_works_cache / author_scores tables."""

    def __init__(self, conn, on_discover=None):
        super().__init__()
        self.conn = conn
        # Optional zero-arg callback that opens the Discover window —
        # supplied by BrowserWindow so the empty-trail state can
        # offer a way to find a first author.
        self._on_discover = on_discover
        self.set_title("Alexandria: Authors")
        self.set_default_size(1000, 720)
        self._pages = {}   # trail key -> AuthorPage
        self._rows = {}    # trail key -> Gtk.ListBoxRow
        # One setting for every catalogue, because the trail is shared
        # across them. Read before the sidebar is built.
        self._sort = _prefs.get_author_trail_sort()

        self.split = Adw.NavigationSplitView()
        self.split.set_min_sidebar_width(240)
        self.split.set_max_sidebar_width(340)

        self.sidebar = Gtk.ListBox()
        self.sidebar.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.sidebar.add_css_class("navigation-sidebar")
        self.sidebar.connect("row-selected", self._on_row_selected)
        side_scroll = Gtk.ScrolledWindow()
        side_scroll.set_policy(Gtk.PolicyType.NEVER,
                               Gtk.PolicyType.AUTOMATIC)
        side_scroll.set_child(self.sidebar)
        side_tb = Adw.ToolbarView()
        side_header = Adw.HeaderBar()
        side_header.pack_end(self._build_sort_button())
        side_tb.add_top_bar(side_header)
        side_tb.set_content(side_scroll)
        self.split.set_sidebar(
            Adw.NavigationPage.new(side_tb, "Alexandria: Authors"))

        self.stack = Gtk.Stack()
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        # Empty-pane state: "select one" when the trail has rows,
        # a get-started hint (with a Discover shortcut when the
        # opener provided one) when it is empty — the menu-opened
        # window on a fresh library must not be a blank pane.
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                        spacing=12)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        self._empty_lbl = Gtk.Label(justify=Gtk.Justification.CENTER)
        self._empty_lbl.set_wrap(True)
        empty.append(self._empty_lbl)
        self._empty_discover_btn = Gtk.Button(label="Open Discover…")
        self._empty_discover_btn.set_halign(Gtk.Align.CENTER)
        self._empty_discover_btn.set_visible(False)
        self._empty_discover_btn.connect(
            "clicked",
            lambda _b: self._on_discover() if self._on_discover
            else None)
        empty.append(self._empty_discover_btn)
        self.stack.add_named(empty, "empty")
        content_tb = Adw.ToolbarView()
        content_tb.add_top_bar(Adw.HeaderBar())
        content_tb.set_content(self.stack)
        # The content page's title tracks the selected author (shown
        # in the content header bar) — see _on_row_selected.
        self._content_page = Adw.NavigationPage.new(content_tb, "Authors")
        self.split.set_content(self._content_page)

        self.set_content(self.split)

        # Ctrl+F reaches the find bar on whichever author is showing —
        # the same key the library window uses for its own search, so
        # the habit carries across. GLOBAL scope because the focus is
        # usually in the sidebar or on nothing in particular, not
        # inside the page that owns the entry.
        finder = Gtk.ShortcutController()
        finder.set_scope(Gtk.ShortcutScope.GLOBAL)
        finder.add_shortcut(Gtk.Shortcut.new(
            Gtk.ShortcutTrigger.parse_string("<Control>f"),
            Gtk.CallbackAction.new(lambda *_a: self._focus_find())))
        self.add_controller(finder)

        # Below 640sp the split view collapses to a navigation stack:
        # the sidebar fills the window and selecting an author pushes
        # their page with a back button.
        bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse("max-width: 640sp"))
        bp.add_setter(self.split, "collapsed", True)
        self.add_breakpoint(bp)

        # Rebuild the sidebar from the persisted trail. No pages are
        # created (and nothing is fetched) until a row is selected.
        for entry in index.list_author_trail(self.conn, self._sort):
            self._append_row(entry)
        self._update_empty_state()

    def _focus_find(self):
        """Put the cursor in the visible author page's find box."""
        name = self.stack.get_visible_child_name()
        page = self._pages.get(name)
        if page is None:
            return False
        page._find_entry.grab_focus()
        return True

    # --- Sidebar ordering ---------------------------------------------

    # (action target, menu label). "Custom" leads because it is the
    # default and because it is the only one the user authors.
    _SORT_LABELS = (
        ("custom", "Custom (drag to arrange)"),
        ("surname", "Surname"),
        ("added", "Date added"),
        ("first_publication", "First publication"),
    )

    def _build_sort_button(self):
        """The sidebar header's sort menu.

        A menu rather than the segmented control the works list uses:
        four options with labels this long do not fit a 240px sidebar,
        and a stateful action renders them as radio items for free, so
        the current order is visible without a second widget saying
        so."""
        action = Gio.SimpleAction.new_stateful(
            "sort", GLib.VariantType.new("s"),
            GLib.Variant.new_string(self._sort))
        action.connect("activate", self._on_sort_action)
        group = Gio.SimpleActionGroup()
        group.add_action(action)
        self.insert_action_group("trail", group)
        self._sort_action = action

        menu = Gio.Menu()
        for value, label in self._SORT_LABELS:
            item = Gio.MenuItem.new(label, None)
            item.set_action_and_target_value(
                "trail.sort", GLib.Variant.new_string(value))
            menu.append_item(item)

        btn = Gtk.MenuButton()
        btn.set_icon_name("view-sort-descending-symbolic")
        btn.set_tooltip_text("Sort the author list")
        btn.set_menu_model(menu)
        return btn

    def _on_sort_action(self, action, param):
        action.set_state(param)
        self._set_sort(param.get_string())

    def _set_sort(self, order):
        """Switch order, remember it, redraw."""
        if order == self._sort or order not in index.TRAIL_SORTS:
            return
        self._sort = order
        _prefs.set_author_trail_sort(order)
        self._rebuild_sidebar()
        if order == "first_publication":
            self._start_first_year_backfill()

    def _start_first_year_backfill(self):
        """Fetch the missing career-start years, once per session.

        Without this the sort is honest but useless on first use: a
        year is cached only when an author's page has been opened, so a
        trail of 27 people visited twice sorts 25 of them as "unknown"
        and the order collapses to alphabetical. One profile request per
        missing author, in a thread, with the sidebar re-sorting as the
        answers land — 27 requests for a trail that size, which is why
        it happens on demand rather than at startup."""
        if getattr(self, "_first_year_backfill_done", False):
            return
        missing = [r for r in index.list_author_trail(self.conn)
                   if not r.get("first_publication_year")
                   and (r.get("openalex_id") or r.get("orcid"))]
        if not missing:
            return
        self._first_year_backfill_done = True
        threading.Thread(target=self._first_year_worker,
                         args=(missing,), daemon=True).start()

    def _first_year_worker(self, rows):
        path = index.db_path_of(self.conn)
        if not path:
            return
        conn = index.connect_existing(path)
        try:
            found = 0
            for row in rows:
                # The breaker is the one signal that says "stop asking"
                # — an unkeyed or rate-limited session should not spend
                # 27 doomed requests to sort a list.
                if metrics.openalex_paused_until() > 0:
                    break
                profile = metrics.fetch_author_profile(
                    orcid=row.get("orcid"),
                    openalex_id=row.get("openalex_id"))
                years = [r.get("year")
                         for r in ((profile or {}).get("counts_by_year") or [])
                         if r.get("works_count") and r.get("year")]
                if not years:
                    continue
                if index.set_author_first_publication_year(
                        conn, row["key"], min(years)):
                    found += 1
            if found:
                GLib.idle_add(self._refresh_if_sorted_by_first_publication)
        except Exception:
            pass          # the sidebar still works, just less sorted
        finally:
            conn.close()

    def _refresh_if_sorted_by_first_publication(self):
        """Re-sort only if the user is still looking at that order —
        the backfill takes seconds and they may have moved on."""
        if self._sort == "first_publication":
            self._rebuild_sidebar()
        return False

    def _commit_displayed_order_as_custom(self):
        """Freeze what is on screen into `position`, and switch to
        Custom — without redrawing, because the rows are already in the
        order being frozen."""
        keys = []
        row = self.sidebar.get_row_at_index(0)
        i = 0
        while row is not None:
            keys.append(row.trail_entry["key"])
            i += 1
            row = self.sidebar.get_row_at_index(i)
        index.set_author_trail_order(self.conn, keys)
        self._sort = "custom"
        _prefs.set_author_trail_sort("custom")
        if getattr(self, "_sort_action", None) is not None:
            self._sort_action.set_state(GLib.Variant.new_string("custom"))

    def _rebuild_sidebar(self):
        """Redraw the rows in the current order, keeping the selection.

        Pages are cached in `self._pages` by key, so reselecting the
        open author shows the same page rather than re-fetching it —
        which is what makes switching order cheap enough to be a
        casual act."""
        selected = self.sidebar.get_selected_row()
        selected_key = (selected.trail_entry["key"]
                        if selected is not None else None)
        for row in list(self._rows.values()):
            self.sidebar.remove(row)
        self._rows.clear()
        for entry in index.list_author_trail(self.conn, self._sort):
            self._append_row(entry)
        if selected_key and selected_key in self._rows:
            self.sidebar.select_row(self._rows[selected_key])
        self._update_empty_state()

    def _update_empty_state(self):
        if self._rows:
            self._empty_lbl.set_markup(
                "<span alpha='60%'>Select an author</span>")
            self._empty_discover_btn.set_visible(False)
        else:
            self._empty_lbl.set_markup(
                "<span alpha='60%'>No authors yet — click an "
                "author's name on a paper,\nor find one in "
                "Discover.</span>")
            self._empty_discover_btn.set_visible(
                self._on_discover is not None)

    # --- Sidebar rows -------------------------------------------------

    def _append_row(self, entry):
        """Add one sidebar row for a trail entry (dict with at least
        `key` and `name`). Does not select it."""
        row = Gtk.ListBoxRow()
        row.trail_entry = entry

        # Drag-to-reorder: each row is both a drag source (payload:
        # its trail key) and a drop target. Dropping on a row's upper
        # half inserts before it, lower half after — the standard
        # list-reorder feel. Click-to-select is unaffected (drags
        # only start past the movement threshold).
        drag = Gtk.DragSource.new()
        drag.set_actions(Gdk.DragAction.MOVE)
        drag.connect(
            "prepare",
            lambda _s, _x, _y, k=entry["key"]:
                Gdk.ContentProvider.new_for_value(k))
        row.add_controller(drag)

        drop = Gtk.DropTarget.new(GObject.TYPE_STRING,
                                  Gdk.DragAction.MOVE)
        drop.connect("drop", self._on_row_drop, row)
        row.add_controller(drop)

        hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        hbox.set_margin_top(4)
        hbox.set_margin_bottom(4)
        hbox.set_margin_start(6)
        hbox.set_margin_end(2)

        avatar = Adw.Avatar.new(32, entry.get("name") or None, True)
        img_path = author_image.image_path(entry)
        if img_path and os.path.isfile(img_path):
            try:
                avatar.set_custom_image(Gdk.Texture.new_from_file(
                    Gio.File.new_for_path(img_path)))
            except Exception:
                pass
        hbox.append(avatar)
        row.avatar = avatar

        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        vbox.set_hexpand(True)
        name_lbl = Gtk.Label(xalign=0.0)
        name_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        name_lbl.set_markup(GLib.markup_escape_text(
            entry.get("name") or "Unknown author"))
        vbox.append(name_lbl)
        # Always build the institution label (hidden when unknown) so
        # a profile-fetch backfill can fill it in without rebuilding
        # the row — see _on_page_institution.
        inst_lbl = Gtk.Label(xalign=0.0)
        inst_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        inst_lbl.set_visible(False)
        if entry.get("institution"):
            inst_lbl.set_markup(
                "<span size='small' alpha='65%'>{}</span>".format(
                    GLib.markup_escape_text(entry["institution"])))
            inst_lbl.set_visible(True)
        vbox.append(inst_lbl)
        row.inst_lbl = inst_lbl
        hbox.append(vbox)

        close_btn = Gtk.Button.new_from_icon_name(
            "window-close-symbolic")
        close_btn.add_css_class("flat")
        close_btn.set_valign(Gtk.Align.CENTER)
        close_btn.set_tooltip_text("Remove from the list")
        close_btn.connect(
            "clicked",
            lambda _b, k=entry["key"]: self._remove_author(k))
        hbox.append(close_btn)

        row.set_child(hbox)
        self.sidebar.append(row)
        self._rows[entry["key"]] = row
        self._update_empty_state()
        return row

    def _on_row_selected(self, _listbox, row):
        if row is None:
            self.stack.set_visible_child_name("empty")
            self._content_page.set_title("Authors")
            return
        entry = row.trail_entry
        key = entry["key"]
        page = self._pages.get(key)
        if page is None:
            authorship = {
                "name": entry.get("name"),
                "orcid": entry.get("orcid"),
                "openalex_id": entry.get("openalex_id"),
                "institution": entry.get("institution"),
            }
            scroll = Gtk.ScrolledWindow()
            scroll.set_policy(Gtk.PolicyType.NEVER,
                              Gtk.PolicyType.AUTOMATIC)
            page = AuthorPage(
                self.conn, authorship,
                on_institution=lambda inst, k=key:
                    self._on_page_institution(k, inst),
                on_image_changed=lambda path, k=key:
                    self._on_page_image(k, path))
            scroll.set_child(page)
            self._pages[key] = page
            self.stack.add_named(scroll, key)
        self.stack.set_visible_child_name(key)
        self._content_page.set_title(entry.get("name") or "Author")
        # In the collapsed (narrow) state the panes are a navigation
        # stack — selecting an author must also navigate to the page.
        # A no-op when the panes are side by side.
        self.split.set_show_content(True)
        index.touch_author_trail(self.conn, key)

    def _on_page_institution(self, key, institution):
        """A page's profile fetch learned the author's current
        affiliation (collaborator-chip opens start without one).
        Persist it on the trail row and fill in the sidebar label.
        Runs on the main thread (the page calls this from an
        idle_add handler). The `_rows` guard matters: if the user
        removed the author while the fetch was in flight, writing
        the trail row back would resurrect it."""
        row = self._rows.get(key)
        if row is None or not institution:
            return
        entry = index.add_author_trail(
            self.conn, dict(row.trail_entry, institution=institution))
        if entry is None:
            return
        row.trail_entry = entry
        row.inst_lbl.set_markup(
            "<span size='small' alpha='65%'>{}</span>".format(
                GLib.markup_escape_text(institution)))
        row.inst_lbl.set_visible(True)

    def _on_page_image(self, key, path):
        """A page's photo changed (fetched, chosen, dropped, or
        removed) — mirror it on the sidebar row. Runs on the main
        thread (pages invoke the callback from idle handlers)."""
        row = self._rows.get(key)
        if row is None or not hasattr(row, "avatar"):
            return
        texture = None
        if path and os.path.isfile(path):
            try:
                texture = Gdk.Texture.new_from_file(
                    Gio.File.new_for_path(path))
            except Exception:
                texture = None
        row.avatar.set_custom_image(texture)

    def _remove_author(self, key):
        """The sidebar ×: forget the author (trail row, sidebar row,
        page). Selection falls to a neighbouring row when the removed
        one was selected."""
        index.remove_author_trail(self.conn, key)
        row = self._rows.pop(key, None)
        was_selected = (row is not None
                        and self.sidebar.get_selected_row() is row)
        if row is not None:
            idx = row.get_index()
            self.sidebar.remove(row)
        page = self._pages.pop(key, None)
        if page is not None:
            child = self.stack.get_child_by_name(key)
            if child is not None:
                self.stack.remove(child)
        if was_selected:
            nxt = (self.sidebar.get_row_at_index(idx)
                   or self.sidebar.get_row_at_index(idx - 1))
            if nxt is not None:
                self.sidebar.select_row(nxt)
            else:
                self.stack.set_visible_child_name("empty")
                self._content_page.set_title("Authors")
        self._update_empty_state()

    # --- Public API ---------------------------------------------------

    def _on_row_drop(self, _target, value, _x, y, row):
        """A trail row was dropped onto `row`. Insert before or
        after depending on which half of the row the drop landed
        in."""
        key = value if isinstance(value, str) else None
        if not key or key not in self._rows:
            return False
        idx = row.get_index()
        if y > row.get_allocated_height() / 2:
            idx += 1
        self._reorder(key, idx)
        return True

    def _reorder(self, key, insert_idx):
        """Move `key`'s row to `insert_idx` (an index in the current
        row order, before removal) — widget move + persistent
        position rewrite in one place."""
        # A drag inside a sorted view is an explicit request for a
        # hand-made order, so take it as one: write the displayed order
        # into `position` and switch to Custom before moving anything.
        # Otherwise `move_author_trail` would renumber against
        # positions the user cannot see, quietly scrambling the
        # arrangement they built under Custom.
        if self._sort != "custom":
            self._commit_displayed_order_as_custom()
        row = self._rows[key]
        cur = row.get_index()
        if cur < insert_idx:
            insert_idx -= 1   # removal shifts everything below up
        if insert_idx == cur:
            return
        was_selected = self.sidebar.get_selected_row() is row
        self.sidebar.remove(row)
        self.sidebar.insert(row, insert_idx)
        index.move_author_trail(self.conn, key, insert_idx)
        if was_selected:
            # Removing the selected row emitted row-selected(None)
            # (empty pane); reselecting restores the page.
            self.sidebar.select_row(row)

    def _scroll_row_into_view(self, row):
        """Selected rows must be visible — with a long trail, a new
        author appends (and selects) at the bottom, off-screen.
        Gtk.Viewport.scroll_to needs GTK 4.12; fall back to focusing
        the row, which also scrolls it in."""
        viewport = self.sidebar.get_parent()
        if hasattr(viewport, "scroll_to"):
            viewport.scroll_to(row, None)
        else:
            row.grab_focus()
        return False

    def show_author(self, authorship):
        """Route an author into the window: upsert onto the trail,
        create the sidebar row if new, select it (which lazily builds
        the page)."""
        entry = index.add_author_trail(self.conn, authorship)
        if entry is None:
            return
        row = self._rows.get(entry["key"])
        if row is None:
            row = self._append_row(entry)
        else:
            # Keep row.trail_entry current so restored sessions and later
            # lookups see backfilled fields. The institution label also
            # updates live when the page's profile fetch reports one —
            # see _on_page_institution.
            if row.trail_entry != entry:
                row.trail_entry = entry
        self.sidebar.select_row(row)
        # idle: a freshly appended row has no allocation yet, so an
        # immediate scroll_to lands short of the real bottom.
        GLib.idle_add(self._scroll_row_into_view, row)

    def refresh_in_library(self):
        """Fan the in-library badge refresh out to every live page.
        Called by BrowserWindow._do_debounced_reload after imports."""
        for page in self._pages.values():
            page.refresh_in_library()

    def set_catalogue(self, conn):
        """Point the per-catalogue half of this window at `conn`.

        There is one Authors window per process, but it can be
        reached from several BrowserWindows, each on a different
        catalogue. The author himself is the same person whichever
        window you came from — the trail, scores, works and photos
        all live in the shared authors database now. The one question
        that *does* differ is "which of these papers do I hold", and
        with it the '✓ in library' badges and where Add to Archive
        would import. Those follow the window you clicked from, which
        is the only reading that makes the answer mean anything.

        Before this, the connection captured at creation time was
        kept for the life of the window, so clicking an author on a
        moorhen card could tell you about the default library."""
        if conn is None or conn is self.conn:
            return
        self.conn = conn
        for page in self._pages.values():
            page.conn = conn
        self.refresh_in_library()


# The one Authors window (per process). Recreated on demand after
# the user closes it; the trail table repopulates the sidebar.
_authors_window = None


def _ensure_window(parent, conn):
    """Create the singleton AuthorsWindow if needed and return it."""
    global _authors_window
    if _authors_window is None:
        # Wire the empty-state Discover shortcut when the opener can
        # provide one (BrowserWindow can; viewer/discover callers
        # simply don't get the button).
        on_discover = None
        opener = getattr(parent, "_open_discover", None)
        if opener is not None:
            on_discover = lambda: opener(None)
        win = AuthorsWindow(conn, on_discover=on_discover)

        def _on_close(_w):
            global _authors_window
            _authors_window = None
            return False
        win.connect("close-request", _on_close)
        _authors_window = win
    else:
        # Reached from a second BrowserWindow: adopt its catalogue for
        # the questions that are per-catalogue.
        _authors_window.set_catalogue(conn)
    # Register with the BrowserWindow (when it supports it) so an
    # import landing elsewhere can live-refresh in-library badges.
    # Outside the creation branch, so a window reached from a second
    # BrowserWindow gets that one's imports too; registration is
    # idempotent.
    reg = getattr(parent, "_register_author_window", None)
    if reg is not None:
        reg(_authors_window)
    return _authors_window


def open_trail_window(parent, conn):
    """Open (or raise) the Authors window on the persisted trail
    with no particular author — the hamburger-menu entry point."""
    win = _ensure_window(parent, conn)
    win.present()
    return win


def open_window(parent, conn, authorship):
    """Route `authorship` into the singleton AuthorsWindow, creating
    it on first use. Same signature as the historical
    one-window-per-author implementation, so the browse popover,
    Discover, and collaborator chips all work unchanged."""
    if not (authorship.get("orcid") or authorship.get("openalex_id")):
        # No usable identifier — caller should have checked, but be safe.
        dlg = Gtk.AlertDialog()
        dlg.set_message(
            "No ORCID or OpenAlex ID available for {}".format(
                authorship.get("name") or "this author"))
        dlg.show(parent)
        return None
    win = _ensure_window(parent, conn)
    win.show_author(authorship)
    win.present()
    return win
