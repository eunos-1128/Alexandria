"""The keyboard-shortcuts window, and keeping its list truthful.

Alexandria binds two dozen keys and told nobody — seventeen in the
viewer alone, discoverable only by reading `viewer.py`.

The list in `shortcuts.py` is written by hand. Deriving it from the
`Gtk.ShortcutController`s would be tidier and worse: a controller
knows the accelerator and the callback, not what to call the action
in a sentence, and a window listing "_goto" and "_find_step" would be
no better than nothing. The cost of writing it by hand is that it can
drift from the bindings, which is what this file is for.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk  # noqa: E402

from alexandria import shortcuts  # noqa: E402


def _bindings_in(path):
    """Accelerators actually installed by a module, read from its
    `parse_string` calls."""
    with open(os.path.join(ROOT, "alexandria", path)) as fh:
        source = fh.read()
    return set(re.findall(r'parse_string\("([^"]+)"\)', source)) | \
        set(re.findall(r'^\s*\("([^"]+)",\s*lambda', source, re.M))


def test_every_listed_accelerator_parses():
    """A typo here would render as a dead entry in the window."""
    bad = [a for a in shortcuts.accelerators()
           if Gtk.ShortcutTrigger.parse_string(a) is None]

    assert bad == []


def test_the_dialog_builds():
    assert shortcuts.build_dialog() is not None


def test_nothing_is_listed_that_is_not_bound():
    """The worse failure of the two: a window that promises a key
    which does nothing."""
    bound = _bindings_in("viewer.py") | _bindings_in("browse.py")
    listed = set(shortcuts.accelerators())

    assert listed - bound == set()


def test_the_viewers_navigation_keys_are_all_listed():
    """The ones a reader would go looking for. Not every binding
    needs listing: <Meta>bracketleft is a macOS alias for a key
    already shown, <Control>equal is a keyboard-layout workaround,
    and bare plus/minus duplicate the Ctrl forms. Page movement and
    the sidebar do."""
    listed = set(shortcuts.accelerators())

    for accel in ("Page_Up", "Page_Down", "<Control>Home",
                  "<Control>End", "F9", "F3", "<Shift>F3", "Escape"):
        assert accel in listed, accel


def test_sections_are_not_empty():
    for title, items in shortcuts.SHORTCUTS:
        assert items, title
        for description, accel in items:
            assert description and accel
