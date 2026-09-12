"""The keyboard-shortcuts window.

Alexandria binds two dozen keys and told nobody: seventeen in the
viewer alone, and until this existed the only way to find them was to
read `viewer.py`.

The list lives here as data rather than being derived from the
`Gtk.ShortcutController`s that install them. Deriving it would be
tidier and is a trap: a controller knows the accelerator and the
callback, not what to call the action in a sentence, and a window
listing "_goto" and "_find_step" would be worse than none. The cost
is that this file has to be kept in step by hand, which is what the
test alongside it is for — it checks every accelerator here parses,
and that the viewer's own bindings are all accounted for.

`Adw.ShortcutsDialog`, not `Gtk.ShortcutsWindow`: the latter is
deprecated as of GTK 4.18.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402


# (section title, [(description, accelerator), ...])
#
# Accelerators are in `Gtk.ShortcutTrigger.parse_string` form, which
# is also what Adw.ShortcutsItem wants, so the same string is both
# the binding and the label.
SHORTCUTS = [
    ("Library", [
        ("Search the library", "<Control>f"),
    ]),
    ("Reading", [
        ("Previous page", "Page_Up"),
        ("Next page", "Page_Down"),
        ("First page", "<Control>Home"),
        ("Last page", "<Control>End"),
        ("Show or hide the sidebar", "F9"),
    ]),
    ("Zoom", [
        ("Zoom in", "<Control>plus"),
        ("Zoom out", "<Control>minus"),
        ("Actual size", "<Control>0"),
    ]),
    ("Finding text in a PDF", [
        ("Find", "<Control>f"),
        ("Next match", "F3"),
        ("Previous match", "<Shift>F3"),
        ("Clear the search", "Escape"),
    ]),
    ("Following citations", [
        ("Back to where you were", "<Alt>Left"),
    ]),
    ("General", [
        ("Keyboard shortcuts", "<Control>question"),
    ]),
]

# Bound but deliberately absent above, so the list stays short
# enough to read:
#   <Meta>bracketleft   macOS alias for <Alt>Left, already shown
#   <Control>equal      a US-layout workaround for <Control>plus
#   plus / minus        zoom without the modifier; the Ctrl forms
#                       are shown and are the conventional ones


def build_dialog():
    """The populated dialog, ready to `present(parent)`."""
    dialog = Adw.ShortcutsDialog()
    for title, items in SHORTCUTS:
        section = Adw.ShortcutsSection(title=title)
        for description, accelerator in items:
            section.add(Adw.ShortcutsItem(
                title=description, accelerator=accelerator))
        dialog.add(section)
    return dialog


def present(parent):
    """Show the shortcuts for `parent`'s window."""
    build_dialog().present(parent)


def accelerators():
    """Every accelerator listed, for the test that keeps this file
    honest."""
    return [accel for _title, items in SHORTCUTS for _d, accel in items]
