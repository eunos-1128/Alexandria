"""Context menus open on press-and-hold, not only on right-click.

All three context menus — the card's "Cite this paper as…", the PDB
chip's "Open in Coot", the author avatar's "Copy link" — were bound to
button 3 alone. On a laptop trackpad with no secondary click mapped,
and on a touchscreen, they were unreachable; the PDB chip menu was
missed by its own author for that reason.

These tests are about the wiring rather than the menus: that each site
installs both a `Gtk.GestureClick` on the secondary button and a
`Gtk.GestureLongPress`, that the long press is not touch-only (or the
trackpad gains nothing), and that both paths reach the same callback.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

gestures = pytest.importorskip("alexandria.gestures")

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk


def controllers(widget):
    out = []
    for ctrl in widget.observe_controllers():
        out.append(ctrl)
    return out


def _find(widget, cls):
    return [c for c in controllers(widget) if isinstance(c, cls)]


def test_both_gestures_attached():
    label = Gtk.Label(label="5me2")
    gestures.add_context_menu(label, lambda *_: None)

    clicks = _find(label, Gtk.GestureClick)
    presses = _find(label, Gtk.GestureLongPress)
    assert len(clicks) == 1
    assert len(presses) == 1
    assert clicks[0].get_button() == Gdk.BUTTON_SECONDARY


def test_long_press_is_not_touch_only():
    """A touch-only long press would leave the trackpad where it was."""
    label = Gtk.Label()
    _click, press = gestures.add_context_menu(label, lambda *_: None)
    assert press.get_touch_only() is False


def test_both_paths_reach_the_same_callback():
    calls = []
    label = Gtk.Label()
    click, press = gestures.add_context_menu(
        label, lambda g, x, y: calls.append((g, x, y)))

    click.emit("pressed", 1, 11.0, 12.0)
    press.emit("pressed", 13.0, 14.0)

    assert [(x, y) for _g, x, y in calls] == [(11.0, 12.0), (13.0, 14.0)]
    # The gesture is handed on so `get_widget()` still finds the anchor.
    assert [g.get_widget() for g, _x, _y in calls] == [label, label]


def test_capture_phase_is_opt_in():
    """Both gestures move together: a capturing click with a bubbling
    long press would leave the hold to Gtk.Label's own link menu."""
    for capture, expected in ((False, Gtk.PropagationPhase.BUBBLE),
                              (True, Gtk.PropagationPhase.CAPTURE)):
        label = Gtk.Label()
        gestures.add_context_menu(label, lambda *_: None, capture=capture)
        for ctrl in (_find(label, Gtk.GestureClick)
                     + _find(label, Gtk.GestureLongPress)):
            assert ctrl.get_propagation_phase() == expected


def test_card_and_avatar_sites_use_the_helper():
    """The three call sites, by the helper they now share.

    A site that grew its own `GestureClick` again would drop the long
    press silently, and nothing else in the suite would notice."""
    import inspect
    from alexandria import author_works, browse

    card = inspect.getsource(browse.make_card)
    assert "gestures.add_context_menu" in card
    assert "set_button(3)" not in card

    chip = inspect.getsource(browse)
    # The PDB chip keeps its capture phase through the helper.
    assert "capture=True, claim_click=True" in chip

    avatar = inspect.getsource(author_works._attach_copy_link_menu)
    assert "gestures.add_context_menu" in avatar
    assert "BUTTON_SECONDARY" not in avatar
