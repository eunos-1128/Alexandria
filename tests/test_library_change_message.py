"""The status bar names the file, and says when one arrives.

From 2026-10-03. A workshop PDF was deleted from the library folder;
seconds later a bioRxiv preprint was added from the Authors window.
The status bar said:

    Library updated (deleted)

and nothing afterwards. Both halves of that misled:

  * **It did not name the file.** The message was about the deleted
    workshop PDF, but read as the outcome of the download that
    followed it.
  * **The addition said nothing at all.** "Add to Archive" imports the
    PDF itself, so by the time the watcher sees the file the sidecar
    is already written and its pass returns "recent" — the no-op
    self-event case, where the watcher stays quiet by design. Nothing
    else told the main window, so the stale message stood.
"""

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import browse

msg = browse.library_change_message


# ---- naming the file -------------------------------------------------

def test_a_deletion_names_what_went():
    assert msg("deleted", "/home/p/Documents/Alexandria/workshop.pdf") == (
        "Removed from the library: workshop.pdf")


def test_an_addition_says_so():
    assert msg("new", "/lib/2026.02.21.706873.full.pdf") == (
        "Added to the library: 2026.02.21.706873.full.pdf")


@pytest.mark.parametrize("status,expected", [
    ("existing", "Updated in the library: p.pdf"),
    ("duplicate", "Already in the library: p.pdf"),
    ("merged", "Attached to its BibTeX entry: p.pdf"),
])
def test_the_other_outcomes_read_as_sentences(status, expected):
    assert msg(status, "/lib/p.pdf") == expected


def test_the_basename_is_enough():
    """A full library path would push everything else off the bar."""
    assert "/" not in msg("deleted", "/a/very/long/path/to/p.pdf")


def test_a_known_status_without_a_path_still_reads():
    """Reconcile and the sidecar passes carry no single file."""
    assert msg("deleted") == "Removed from the library"


# ---- everything else ------------------------------------------------

@pytest.mark.parametrize("status", [
    "reconcile", "sidecar", "sidecar-refresh", "crossref-backfill",
    "pdb-backfill", "citations",
])
def test_internal_reasons_keep_the_old_sentence(status):
    """These are reload reasons, not things that happened to a paper.
    Phrasing them as "Added to the library" would be a lie."""
    assert msg(status) == "Library updated ({})".format(status)


def test_an_unknown_status_does_not_crash():
    assert msg("", None) == "Library updated ()"
    assert msg(None, None) == "Library updated ()"


# ---- the wiring ------------------------------------------------------

class _Window:
    """`report_library_change` and the debounced reload on a
    stand-in."""

    report_library_change = browse.BrowserWindow.report_library_change
    _on_watcher_change = browse.BrowserWindow._on_watcher_change
    _do_debounced_reload = browse.BrowserWindow._do_debounced_reload

    def __init__(self):
        self.said = []
        self.status = types.SimpleNamespace(
            set_text=lambda t: self.said.append(t))
        self.search = types.SimpleNamespace(get_text=lambda: "")
        self.reloaded = 0
        self._author_windows = ()

    def _reload(self, _q):
        self.reloaded += 1


def test_the_watcher_path_reaches_the_bar():
    win = _Window()

    win._on_watcher_change("deleted", "/lib/workshop.pdf")
    win._do_debounced_reload()

    assert win.said == ["Removed from the library: workshop.pdf"]


def test_an_in_app_import_reaches_the_bar():
    """The case that was silent: Add to Archive imports the file
    itself, so the watcher never speaks for it."""
    win = _Window()

    win.report_library_change("new", "/lib/2026.02.21.706873.full.pdf")
    win._do_debounced_reload()

    assert win.said == [
        "Added to the library: 2026.02.21.706873.full.pdf"]
    assert win.reloaded == 1, "and the cards are redrawn"


def test_a_second_event_replaces_the_first():
    """Debounced: a burst leaves the last event's message, not a
    queue of them."""
    win = _Window()

    win._on_watcher_change("deleted", "/lib/a.pdf")
    win.report_library_change("new", "/lib/b.pdf")
    win._do_debounced_reload()

    assert win.said == ["Added to the library: b.pdf"]


def test_the_authors_window_is_given_the_callback():
    """`_ensure_window` wires it from the opener, the same way it
    wires the Discover shortcut."""
    import inspect

    from alexandria import author_works

    src = inspect.getsource(author_works._ensure_window)

    assert 'getattr(parent, "report_library_change", None)' in src


def test_an_opener_without_one_is_tolerated():
    """The viewer and Discover also open author windows, and neither
    has a status bar to report to."""
    import inspect

    from alexandria import author_works

    sig = inspect.signature(author_works.AuthorsWindow.__init__)

    assert sig.parameters["on_library_change"].default is None
