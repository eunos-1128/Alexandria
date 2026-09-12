"""Context menus that open on right-click *and* on press-and-hold.

Three places in the UI pop a context menu — a paper card, a PDB chip,
an author's avatar — and each had grown its own `Gtk.GestureClick` on
button 3. That is the mouse idiom and only the mouse idiom: on a
trackpad without secondary-click mapped, and on a touchscreen, there
was no way in at all. The PDB chip menu went unnoticed by the person
who wrote it for exactly that reason — he was on a laptop trackpad.

So the two gestures are attached together, here, once.

The long press claims its event sequence when it fires, which does
double duty: it releases the gesture's implicit grab before the
popover is mapped (otherwise GTK complains "Tried to map a grabbing
popup with a non-top most parent"), and it denies the sequence to
every other gesture in the propagation chain, so the hold that opened
the menu does not also trigger whatever sits underneath — a card's
long press opens the menu instead of the PDF.

`Gtk.GestureLongPress` is not touch-only by default, which is what
makes one controller serve both the touchscreen and the trackpad.
"""

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402


def _claim(gesture):
    try:
        gesture.set_state(Gtk.EventSequenceState.CLAIMED)
    except Exception:
        pass


def add_context_menu(widget, show, *, capture=False, claim_click=False):
    """Open a menu on `widget` by right-click or by press-and-hold.

    `show(gesture, x, y)` does the popping up; it is handed whichever
    gesture fired, so `gesture.get_widget()` still finds the anchor.

    `capture` puts both controllers in the capture phase, for a widget
    with a built-in menu of its own to pre-empt (`Gtk.Label` with a
    link). `claim_click` claims the right-click sequence too, which
    the same case needs to stop the built-in handler running.

    Returns the two controllers, for a caller that wants to tune them.
    """
    phase = (Gtk.PropagationPhase.CAPTURE if capture
             else Gtk.PropagationPhase.BUBBLE)

    click = Gtk.GestureClick.new()
    click.set_button(Gdk.BUTTON_SECONDARY)
    click.set_propagation_phase(phase)

    def on_click(gesture, _n_press, x, y):
        if claim_click:
            _claim(gesture)
        show(gesture, x, y)

    click.connect("pressed", on_click)
    widget.add_controller(click)

    press = Gtk.GestureLongPress.new()
    press.set_touch_only(False)
    press.set_propagation_phase(phase)

    def on_long_press(gesture, x, y):
        _claim(gesture)
        show(gesture, x, y)

    press.connect("pressed", on_long_press)
    widget.add_controller(press)

    return click, press
