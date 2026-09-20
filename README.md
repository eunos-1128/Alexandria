# Alexandria

A GTK4-based organizer/reference manager for scientific PDFs.

Alexandria is designed to be a personal and local store. It is not
an interface where you upload your document collection to a cloud
server somewhere. You PDF catalogue won't be (can't be) part of
someone else's data harvesting. There is no crowdsourcing.
If you want your reading habits and project lists tracked by
corporations, and suggested to others then Alexandria is not
for you.

Alexandria has 3 main "views"
  - PDF viewer: for reading and annotations
  - PDF organizer/reference manager: view, store and update metadata
  - Author view: what's been published (recently), who publishes
    with or cites who?

There is a PDF viewer, based on poppler [1] built-in. One can add
annotations to PDFs.

Alexandria uses OpenAlex and CrossRef network calls and PDF text
extraction to associate metadata [2] with PDF files (`.alexandria`
extension, but JSON inside) - these are the "sidecars."

It would not be very wrong to describe Alexandria as a desktop
interface to OpenAlex that knows about PDF files and citation
formats.

The file store is a plain old directory with PDF files in it.

An SQLite database is constructed using the sidecars for fast
searching.

There are subscriptions and discovery.

Alexandria uses JATS where it can.

Alexandria is intended to be XDG Base Directory Protocol [3] compliant. It
writes, by default, to `$HOME/Documents/Alexandria` and the database to
`$HOME/.local/state/Alexandria` with a config file
in `$HOME/.config/Alexandria/config.json` for sort order config and
OpenAlex key.

## Background

Quite some time ago now I had liked John Spray's Referencer:

[https://icculus.org/referencer](https://icculus.org/referencer)

as a local-storage pdf organizer/reference manager. But that seems to no
longer be maintained or updated. Which is why I thought.. maybe I can
make one...

## > [!CAUTION]

> If you cloned Alexandria before 0.5.0 then it's probably best
> to throw away the previous repo and start fresh.
>
> I rewrote the history and removed and recreated the repo to remove
> information that should not have been there.

## Screenshots
![Alexandria main window example](data/screenshots/main-window-screenshot.png)
![Alexandria author view example](data/screenshots/author-window-screenshot.png)


## Install

### Debian / Ubuntu

    apt install python3-gi gir1.2-gtk-4.0 gir1.2-poppler-0.18 poppler-utils
    make install

### Arch Linux

The GTK stack and PyGObject come from pacman — PyGObject has to be
built against the same GTK it will load, so mixing a pip-installed
one with the system libraries is the way to a segfault:

    sudo pacman -S --needed gtk4 libadwaita poppler poppler-glib \
                            python-gobject python-cairo \
                            adwaita-icon-theme librsvg vte4

`poppler-glib` carries the `Poppler-0.18` typelib the viewer imports;
`poppler` provides `pdftotext`, which the text extraction shells out
to. `adwaita-icon-theme` and `librsvg` are what make the toolbar
render as icons rather than broken-image squares. `vte4` is only for
the optional built-in terminal.

The Python dependencies come from pip, and cannot come from pacman:
Arch ships `python-bibtexparser` 1.4.4, while `bibtex.py` uses the
v2 `parse_string` / middlewares API, and `pdfx` and `citeproc-py` are
not in the repositories at all.

Arch also marks its system Python as externally managed (PEP 668), so
`pip install --user` refuses to run. Use a virtualenv that can still
see pacman's PyGObject:

    python -m venv --system-site-packages ~/.venvs/alexandria
    make install PYTHON=~/.venvs/alexandria/bin/python

`--system-site-packages` is the load-bearing flag: without it the
venv cannot see `gi` at all, and pip will try to build PyGObject from
source to fill the gap. With it, pip installs only the pure-Python
dependencies and leaves the GTK bindings to pacman.

Then run `~/.venvs/alexandria/bin/alexandria-browse`, or put
`~/.venvs/alexandria/bin` on your `PATH`. The `.desktop` file
`make install` writes calls `alexandria-browse`, so either the venv's
`bin` needs to be on the PATH your desktop session sees, or edit the
`Exec=` line to the absolute path.

To run from the source tree instead, no install is needed once the
pacman packages above are present and the pip dependencies are in
the venv:

    ~/.venvs/alexandria/bin/python alexandria-browse.py

### macOS (Homebrew)

Alexandria is a GTK4 application, so the GTK stack and its
GObject-introspection typelibs have to come from Homebrew — there is
no wheel for them.

    brew install gtk4 libadwaita poppler gobject-introspection \
                 librsvg vte3 adwaita-icon-theme
    brew install pygobject3 py3cairo

    pip install --user .          # or: make install

Use the same Python throughout. Homebrew's `pygobject3` and
`py3cairo` are built for Homebrew's Python, so `pip install` with
that interpreter. If you would rather use your own Python, skip
those two formulae and let pip build PyGObject and pycairo from
source — that works, but only because the Homebrew C libraries
above are already present for it to compile against.

`adwaita-icon-theme` is not optional. Without it GTK falls back to its
small built-in icon set and much of the toolbar renders as
broken-image squares. `librsvg` is what draws SVG icons at all, and
`vte3` provides the optional built-in terminal.

To run from the source tree:

    export XDG_DATA_DIRS=/opt/homebrew/share
    export DYLD_LIBRARY_PATH=/opt/homebrew/lib
    python3 alexandria-browse.py

Both variables are needed because Homebrew installs outside the paths
GLib searches by default. `XDG_DATA_DIRS` in particular is not
optional: without it `Gtk.FileDialog` aborts with "No GSettings
schemas are installed on the system" the first time you open a file.

Or build a `.app` bundle, which gives Alexandria its own Dock icon and
identity instead of inheriting Python's:

    ./make-app.sh          # produces dist/Alexandria.app

Note that `make install` also installs a `.desktop` file and an XDG
icon, which do nothing on macOS but are harmless.

## Configuration

On first run Alexandria writes a starter config file to
`$HOME/.config/Alexandria/config.json`:

    {
      "catalogues": [
        { "name": "default", "library_root": "/home/you/Documents/Alexandria" }
      ],
      "current_catalogue": "default",
      "contact_email": "",
      "openalex_api_key": ""
    }

Everything works with both of those left empty, but neither is
decoration:

**`contact_email`** is your own address, and it is what the metadata
services ask for so they can contact you about a misbehaving client.
OpenAlex and CrossRef give politer rate limits to requests that carry
one — without it you share the anonymous pool, which matters most when
importing a folder of papers. **Unpaywall refuses to answer at all**
without an address, so until you set one, **Get PDF** quietly searches
one source fewer.

**`openalex_api_key`** is free from
[openalex.org](https://openalex.org/) and gives you a private daily
request budget instead of the shared one. Author pages, citation
counts, most of Discover and the reference popover all spend it — but
not Discover's **Preprints** tab, which asks Europe PMC, or **By PDB**,
which asks PDBe; neither needs a key. Without one you are not locked
out, but on a large library you will meet the common pool's rate
limit.

Both can also be set in **Preferences → Online services** rather than
by editing the file, and the file is the same file. The library root
can be overridden for one run with the `ALEXANDRIA_LIBRARY`
environment variable.

## Usage

Upon opening Alexandria, it will detect PDF files in
`$HOME/Documents/Alexandria` and try to create a thumbnail PNG and the
associated metadata (if they don't already exist).

### Getting papers in

Drop a PDF into `$HOME/Documents/Alexandria` with a file manager, or
drag it onto the Alexandria window — either way a card appears, with
the metadata looked up from the DOI. There is no import dialog to go
through. The hamburger menu also offers **Import from DOI or PubMed
ID…**, **Import BibTeX…**, **Import Files…** and **Import Folder…**.

A BibTeX import makes a card for every entry, including the ones with
no PDF. Those are *ghost* cards: press **Get PDF** and Alexandria
tries OpenAlex, Unpaywall and EuropePMC in turn, and on success merges
the download into the entry. Dropping a PDF straight onto a ghost
card's thumbnail does the same thing.

### Reading

Click a card's thumbnail to open the viewer. The sidebar has three
modes — **Contents** (the PDF's own table of contents), **Pages**
(thumbnails) and **Highlights** — and F9 toggles it.

The part worth knowing about: **click a citation marker in the body
text**. Alexandria resolves `[12]` against the paper's reference list
and shows the reference right where you are reading — the entry, the
sentence it was cited in, its metadata and an option to add it to
your library — so there is nothing to scroll back from. **Go to
reference** makes the trip to the bibliography when you want it, and
Alt-Left brings you back. This works from the publisher's own
link annotations where they exist, and from the JATS full text or the
printed reference list where they do not.

Select text to highlight it and attach a comment; highlights are
saved in the sidecar and listed in the sidebar.

### Cards

The chips along a card's title tell you about the paper at a glance:
open-access status, licence, whether a correction or retraction has
been registered, whether the full text or JATS is stored locally,
whether it is supplementary material, and — for UK-funded work — the
UKRI grant that paid for it. Hover any of them for the detail.

A **Check metadata** chip means the DOI's record was used but it
disagrees with what the PDF says about itself; **Unverified** means no
DOI resolved to a paper record at all. Either way, open **Edit
metadata** to see both and decide.

### Authors

Click the author list on a card for a popover of its authors, then a
name to open the Author view: what they have published, who they
publish with, who cites them most, their
citing-impact split by whether the citing paper treats the work as
software, method or idea — and, where they are UK-funded, the grants
they hold as PI or Co-I with amounts and dates, from UKRI Gateway to
Research.

Authors you have looked at accumulate in a trail down the left, and
they are shared across catalogues, as are any photographs you attach.

### Keeping up

**Subscriptions** follow a journal, a saved OpenAlex search, or
bioRxiv subject collections, and collect what is new into a feed
refreshed in the background.

bioRxiv has no search, so its subscriptions are whole subjects: tick
as many of the 27 collections as you read and each gets its own feed,
with a preprint listed under two subjects appearing under both. The
rows arrive complete — title, authors, abstract, DOI and date come
straight from the feed, with no further lookups. To search preprints
by term instead, use **Discover → Preprints**, which asks Europe
PMC.

## Notes
- [1] poppler `https://poppler.freedesktop.org/`
- [2] titles, authors, journal, year, DOI, comments
- [3] `https://specifications.freedesktop.org/basedir/latest/`

