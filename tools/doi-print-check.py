#!/usr/bin/env python3
"""Find PDFs whose printed DOI disagrees with their own link target.

A publisher's front matter carries the DOI twice: as text on the
page, and as the URL behind that text. When they disagree, either the
file is inconsistent or — far more likely, as it turned out here —
our text extraction is at fault. Needing no reference DOI from
outside the file is the point: each report is self-contained.

Written 2026-09-08 believing Annual Reviews shipped a broken DOI in
their text layer. They do not. Their DOIs wrap across a line at a
hyphen, and `pdftotext` in its default mode joins such lines and
drops the hyphen. This tool reported 7 of 7 files as mismatched;
after `_scan_doi_in_pages` moved to `-raw`, it reports 0 of 7. That
is what it is for now: a check that our own extraction agrees with
what the publisher linked.

    tools/doi-print-check.py FILE.pdf [FILE.pdf ...]
    tools/doi-print-check.py ~/Documents/Alexandria      # a directory
    tools/doi-print-check.py --all ~/Desktop            # list every file
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import gi  # noqa: E402
gi.require_version("Poppler", "0.18")
from gi.repository import Poppler, Gio  # noqa: E402

from alexandria import extract  # noqa: E402

_DOI_IN_URL = re.compile(r"10\.\d{4,9}/[-._;()/:A-Za-z0-9]+")


def link_dois(pdf_path, max_pages=2):
    """DOIs found in the link annotations of the first pages."""
    out = []
    try:
        uri = Gio.File.new_for_path(pdf_path).get_uri()
        doc = Poppler.Document.new_from_file(uri, None)
    except Exception:
        return out
    for i in range(min(max_pages, doc.get_n_pages())):
        try:
            page = doc.get_page(i)
            mapping = page.get_link_mapping() or []
        except Exception:
            continue
        for m in mapping:
            action = getattr(m, "action", None)
            if action is None or action.type != Poppler.ActionType.URI:
                continue
            try:
                url = action.uri.uri or ""
            except Exception:
                continue
            found = _DOI_IN_URL.search(url)
            if found:
                doi = found.group(0).rstrip(".,;)]")
                if doi not in out:
                    out.append(doi)
    return out


def check(pdf_path):
    """(verdict, printed, linked) — verdict is 'mismatch', 'ok',
    'no text doi', 'no link doi'."""
    try:
        printed = extract._scan_doi_in_pages(pdf_path, max_pages=2)
    except Exception:
        printed = None
    linked = link_dois(pdf_path)
    if not printed:
        return "no text doi", printed, linked
    if not linked:
        return "no link doi", printed, linked
    if any(d.lower() == printed.lower() for d in linked):
        return "ok", printed, linked
    return "mismatch", printed, linked


def pdfs_under(path):
    if os.path.isfile(path):
        return [path]
    out = []
    for dirpath, _dirs, files in os.walk(path):
        for name in sorted(files):
            if name.lower().endswith(".pdf") and not name.startswith("._"):
                out.append(os.path.join(dirpath, name))
    return out


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("--")]
    show_all = "--all" in argv[1:]
    if not args:
        print(__doc__)
        return 2
    targets = []
    for a in args:
        targets.extend(pdfs_under(a))
    if not targets:
        print("no PDFs found")
        return 1
    bad = 0
    for path in targets:
        verdict, printed, linked = check(path)
        if verdict == "mismatch":
            bad += 1
            print("MISMATCH  " + os.path.basename(path))
            print("   printed on the page : " + str(printed))
            print("   link points at      : " + ", ".join(linked[:3]))
            hyphenless = [d for d in linked
                          if d.replace("-", "").lower()
                          == (printed or "").replace("-", "").lower()]
            if hyphenless:
                print("   -> same DOI, hyphen(s) missing from the text")
        elif show_all:
            print("%-9s %s" % (verdict, os.path.basename(path)))
    print()
    print("%d of %d file(s) print a DOI that disagrees with their own link"
          % (bad, len(targets)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
