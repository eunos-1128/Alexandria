"""Help → Show Log: the diagnostics, where the user can find them.

Nearly every fix this autumn began with a terminal. Launched from a
desktop icon — or from Flathub, where the application now lives —
stdout goes nowhere anyone will look, so "it didn't work" has nothing
attached to it. `applog` writes the file; this shows it.

Shown in the application rather than handed to an external viewer.
Under Flatpak the log lives inside the sandbox's own directory, and
asking the portal to open it in somebody's text editor is a round
trip that can simply fail; and the thing the user actually needs to
do with it — select it, copy it, paste it into a bug report — wants a
Copy button, not an editor.
"""

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import applog  # noqa: E402


def open_window(parent):
    win = LogWindow(parent)
    win.present()
    return win


class LogWindow(Adw.Window):
    def __init__(self, parent=None):
        super().__init__()
        if parent is not None:
            self.set_transient_for(parent)
        self.set_title("Alexandria: Log")
        self.set_default_size(900, 620)

        header = Adw.HeaderBar()
        reload_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        reload_btn.set_tooltip_text("Re-read the log file")
        reload_btn.connect("clicked", lambda _b: self.reload())
        header.pack_start(reload_btn)

        copy_btn = Gtk.Button(label="Copy")
        copy_btn.set_tooltip_text("Copy the whole log, to paste into a "
                                  "bug report")
        copy_btn.connect("clicked", self._on_copy)
        header.pack_end(copy_btn)

        self._view = Gtk.TextView()
        self._view.set_editable(False)
        self._view.set_cursor_visible(False)
        self._view.set_monospace(True)
        self._view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._view.set_left_margin(8)
        self._view.set_right_margin(8)
        self._view.set_top_margin(6)

        scroll = Gtk.ScrolledWindow()
        scroll.set_vexpand(True)
        scroll.set_child(self._view)

        # The path, selectable: a user who would rather open the file
        # themselves needs to know where it is, and under Flatpak it
        # is somewhere they would never guess.
        self._path_label = Gtk.Label(xalign=0.0)
        self._path_label.set_selectable(True)
        self._path_label.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        self._path_label.set_margin_start(10)
        self._path_label.set_margin_end(10)
        self._path_label.set_margin_bottom(6)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(header)
        box.append(scroll)
        box.append(self._path_label)
        self.set_content(box)

        self.reload()

    def reload(self):
        """Re-read the tail of the log and scroll to the end — the
        last lines are the ones worth seeing."""
        text = applog.read_tail()
        buf = self._view.get_buffer()
        buf.set_text(text or "The log is empty.")
        self._path_label.set_markup(
            "<span size='small' alpha='65%'>{}</span>".format(
                GLib.markup_escape_text(applog.log_path())))
        # Scrolling has to wait for the view to have laid the text
        # out; an idle pass is enough.
        GLib.idle_add(self._scroll_to_end)

    def _scroll_to_end(self):
        buf = self._view.get_buffer()
        self._view.scroll_to_iter(buf.get_end_iter(), 0.0, True, 0.0, 1.0)
        return False

    def _on_copy(self, _btn):
        buf = self._view.get_buffer()
        self.get_clipboard().set(
            buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
