"""GFileMonitor-driven library watcher.

Monitors the LIBRARY_ROOT directory (flat — no subdirs) for two kinds
of events:

* PDF files: CREATED / CHANGES_DONE_HINT / MOVED_IN → import in a
  background thread. DELETED / MOVED_OUT → drop the index row.
  RENAMED in-place → re-import; SHA-256 detection adopts the row.

* Sidecar files (`*.alexandria`): CHANGED / CHANGES_DONE_HINT /
  CREATED → re-read the sidecar and refresh the index row. This is
  what makes `alexandria-import --refresh` invisibly update the running
  browser — the CLI rewrites the JSON, the watcher sees it, the row
  is upserted, the GUI redraws.
"""

import os
import threading
import time

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib

from . import bibtex_import, importer, index, sidecar


# How long to wait between batched events from the OS. Lets a
# `cp ~/Desktop/*.pdf ~/pdfs/` of 50 PDFs collapse into a few events
# rather than 50 simultaneous import threads.
RATE_LIMIT_MS = 1500


def _is_pdf(path):
    if not path or not path.lower().endswith(".pdf"):
        return False
    # macOS AppleDouble resource forks ("._foo.pdf") are metadata,
    # not PDFs — skip them or every Mac copy-in spams failed imports.
    return not os.path.basename(path).startswith("._")


def _is_sidecar(path):
    return bool(path) and path.endswith(sidecar.SIDECAR_SUFFIX)


class LibraryWatcher:
    """Watches `library_root` for PDF changes and keeps the SQLite index
    in sync. on_change(status_str) is called on the GLib main thread
    after each successful change."""

    def __init__(self, db_path, library_root, on_change_cb=None,
                 on_import_start_cb=None, skip_roots=None,
                 on_sidecar_rejected_cb=None,
                 on_import_progress_cb=None):
        # We take a db_path rather than a sqlite3.Connection because
        # every code path that touches the index runs in its own
        # daemon thread (`_spawn`'d event handlers and the reconcile
        # thread). Sharing one connection across threads segfaults
        # inside Apple's libsqlite3; opening a per-thread connection
        # via index.connect_existing(db_path) keeps each handler
        # single-threaded.
        self.db_path = db_path
        self.root = library_root
        self.on_change = on_change_cb
        self.on_import_start = on_import_start_cb
        # Called with (basename, message) as an import moves through
        # its steps. Unlike the callbacks above it is *not* wrapped in
        # idle_add here: a cold import posts a dozen lines, and the GUI
        # side already hops to the main thread through its ticker.
        self.on_import_progress = on_import_progress_cb
        # Called (on the GLib main thread, with the sidecar basename)
        # when a *.alexandria file appears in the library with no
        # matching PDF — e.g. someone emailed a shared sidecar and it
        # was saved into the library. Importing other people's notes
        # isn't supported yet (see BACKLOG "Sharing"), so the watcher
        # rejects it with a toast rather than silently doing nothing.
        self.on_sidecar_rejected = on_sidecar_rejected_cb
        # Other catalogue roots — passed through to import_tree so
        # the cold reconcile doesn't slurp PDFs that live inside a
        # nested sibling catalogue's folder.
        self.skip_roots = list(skip_roots or [])
        self.monitor = None
        self._reconcile_thread = None
        # abspath -> expiry timestamp. Events on suppressed paths are
        # dropped — used during ghost-merge so the file we're about to
        # copy / re-import / roll back doesn't trigger nested watcher
        # imports.
        self._suppress = {}
        self._suppress_lock = threading.Lock()

    def suppress(self, path, secs):
        """Public: silence watcher events on `path` for `secs` seconds.
        Used by callers that write to a sidecar themselves and don't
        want the resulting CHANGED event to drive a card-list reload
        (e.g. caching cited-by / references lists)."""
        self._suppress_path(path, secs)

    def _suppress_path(self, path, secs):
        if not path:
            return
        with self._suppress_lock:
            self._suppress[os.path.abspath(path)] = time.time() + secs

    def _is_suppressed(self, path):
        if not path:
            return False
        ap = os.path.abspath(path)
        with self._suppress_lock:
            exp = self._suppress.get(ap)
            if exp is None:
                return False
            if exp < time.time():
                self._suppress.pop(ap, None)
                return False
            return True

    # --- Lifecycle ----------------------------------------------------

    def start(self):
        if self.monitor:
            print("[watcher] start: already running")
            return
        # A missing root is a normal state — a fresh library, or the
        # user cleared the folder. Create it and watch it; declining
        # to start here meant nothing ever imported until an app
        # restart with the directory present.
        if not os.path.isdir(self.root):
            try:
                os.makedirs(self.root, exist_ok=True)
                print("[watcher] created library root: {}".format(
                    self.root))
            except OSError as e:
                print("[watcher] start: cannot create root {!r}: {}"
                      .format(self.root, e))
                return
        try:
            gfile = Gio.File.new_for_path(self.root)
            self.monitor = gfile.monitor_directory(
                Gio.FileMonitorFlags.WATCH_MOVES, None)
        except GLib.Error as e:
            print("[watcher] monitor_directory failed:", e)
            self.monitor = None
            return
        self.monitor.set_rate_limit(RATE_LIMIT_MS)
        self.monitor.connect("changed", self._on_changed)
        print("[watcher] watching {} (rate-limit {}ms)".format(
            self.root, RATE_LIMIT_MS))

    def stop(self):
        if self.monitor:
            self.monitor.cancel()
            self.monitor = None

    def reconcile_startup(self):
        """Catch up on PDFs added while the browser was closed.
        Doesn't auto-delete missing entries (a temporarily unmounted
        share would otherwise wipe the index)."""
        if self._reconcile_thread and self._reconcile_thread.is_alive():
            return
        if not os.path.isdir(self.root):
            return
        self._reconcile_thread = threading.Thread(
            target=self._do_reconcile, daemon=True)
        self._reconcile_thread.start()

    def _do_reconcile(self):
        """Catch up on anything that changed while we were not
        watching — and, since the ghost-merge dispatch moved into
        `importer`, rescue PDFs whose live event was dropped.

        Counts what it did. Silence here used to be ambiguous: a
        startup that found nothing and a startup that found a
        problem and did nothing about it looked identical, which is
        precisely how the orphaned-PDF bug stayed hidden."""
        conn = index.connect_existing(self.db_path)
        tally = {}

        def count(_i, _n, _path, _rec, status):
            tally[status] = tally.get(status, 0) + 1

        try:
            importer.import_tree(
                conn, self.root, on_progress=count,
                skip_roots=self.skip_roots)
        except Exception as e:
            print("LibraryWatcher: reconcile failed:", e)
            return
        finally:
            conn.close()
        if tally:
            print("[watcher] reconcile: {}".format(
                ", ".join("{} {}".format(n, status)
                          for status, n in sorted(tally.items()))))
        if self.on_change:
            GLib.idle_add(self.on_change, "reconcile")

    # --- Event handling -----------------------------------------------

    def _on_changed(self, _monitor, gfile, other_file, event_type):
        path = gfile.get_path() if gfile else None
        other = other_file.get_path() if other_file else None

        et = event_type
        et_name = getattr(et, "value_nick", str(et))
        print("[watcher] event={} path={} other={}".format(
            et_name, path, other))
        if self._is_suppressed(path) or self._is_suppressed(other):
            print("[watcher] suppressed (in-flight ghost-merge)")
            return
        if et in (Gio.FileMonitorEvent.CREATED,
                  Gio.FileMonitorEvent.CHANGES_DONE_HINT,
                  Gio.FileMonitorEvent.MOVED_IN):
            if _is_pdf(path):
                self._spawn(self._do_import, path)
            elif _is_sidecar(path):
                self._spawn(self._do_resync_sidecar, path)

        elif et == Gio.FileMonitorEvent.CHANGED:
            # PDFs aren't usually edited in place; sidecars are
            # (e.g. by `alexandria-import --refresh` or hand-edits).
            if _is_sidecar(path):
                self._spawn(self._do_resync_sidecar, path)

        elif et in (Gio.FileMonitorEvent.DELETED,
                    Gio.FileMonitorEvent.MOVED_OUT):
            if _is_pdf(path):
                self._spawn(self._do_delete, path)
            # Sidecar deletions are ignored: most are our own atomic-
            # write temporaries; a real sidecar removal will resolve
            # next time the PDF is imported.

        elif et == Gio.FileMonitorEvent.RENAMED:
            # Re-import at the new path; import_pdf's SHA-256 detection
            # adopts the existing index row.
            if _is_pdf(other):
                self._spawn(self._do_import, other)
            elif _is_sidecar(other):
                # An atomic sidecar write (`sidecar.write` is tmp +
                # os.replace, which surfaces as RENAMED). This used to
                # be ignored on the assumption that any such write was
                # this process's own, and the GUI had already redrawn.
                #
                # That stopped being true once a second Alexandria
                # process could write: the MCP server's `set_summary`
                # writes the sidecar *and* upserts the index, so the
                # database is correct and only the open window is
                # stale. The summary would not appear on the card
                # until a restart.
                self._spawn(self._do_sidecar_refresh, other)
            elif _is_pdf(path):
                # Renamed to non-PDF (e.g. ".pdf.bak") — drop it.
                self._spawn(self._do_delete, path)

    def _do_sidecar_refresh(self, sc_path):
        """Tell the GUI to re-read a sidecar that another writer
        replaced.

        Deliberately does not touch the index: whoever wrote the file
        updated the row, and re-reading it here would race them. The
        only thing missing is that the open window does not know.

        Unknown sidecars are ignored rather than imported — a file
        that matches no paper is the emailed-sidecar case, which
        `_on_foreign_sidecar` handles."""
        if self._is_suppressed(sc_path):
            return
        try:
            conn = index.connect_existing(self.db_path)
        except Exception:
            return
        try:
            known = conn.execute(
                "SELECT 1 FROM papers WHERE sidecar_path = ?",
                (sc_path,)).fetchone() is not None
        except Exception:
            known = False
        finally:
            conn.close()
        if known and self.on_change:
            print("[watcher] sidecar rewritten elsewhere; reloading: {}"
                  .format(os.path.basename(sc_path)))
            GLib.idle_add(self.on_change, "sidecar-refresh")

    def _spawn(self, fn, *args):
        threading.Thread(target=fn, args=args, daemon=True).start()

    def _do_import(self, path):
        print("[watcher] import start: {}".format(path))
        if self.on_import_start:
            GLib.idle_add(self.on_import_start, os.path.basename(path))
        conn = index.connect_existing(self.db_path)
        try:
            self._do_import_with_conn(conn, path)
        finally:
            conn.close()

    def _do_import_with_conn(self, conn, path):
        progress = None
        if self.on_import_progress:
            name = os.path.basename(path)

            def progress(message, _n=name):
                self.on_import_progress(_n, message)
        try:
            # The ghost-merge dispatch lives in importer now, so a
            # reconcile pass gets it too: this branch used to be the
            # only place it happened, and a PDF whose filesystem
            # event was dropped could never be attached afterwards.
            rec, status, new_path = importer.import_pdf_or_attach(
                conn, path, self.root, suppress=self._suppress_path,
                on_progress=progress)
        except Exception as e:
            print("[watcher] import failed for {}: {}".format(path, e))
            return

        if new_path:
            # Lift suppression early on success so legitimate
            # follow-up edits aren't dropped.
            with self._suppress_lock:
                self._suppress.pop(os.path.abspath(new_path), None)
            print("[watcher] ghost-merge: {} -> {} ({})".format(
                path, status, new_path))
            if self.on_change:
                GLib.idle_add(self.on_change, status)
            return

        if status == "duplicate" and rec:
            print("[watcher] import done: {} -> duplicate of {}".format(
                path, rec.get("pdf_path") or "?"))
        else:
            print("[watcher] import done: {} -> {}".format(path, status))
        # "recent" is the no-op self-event case; don't bother the UI.
        if status in ("recent",):
            return
        if self.on_change:
            GLib.idle_add(self.on_change, status)

    def _do_delete(self, path):
        conn = index.connect_existing(self.db_path)
        try:
            importer.delete_pdf(conn, path)
        except Exception as e:
            print("watcher: delete failed for {}: {}".format(path, e))
            return
        finally:
            conn.close()
        if self.on_change:
            GLib.idle_add(self.on_change, "deleted")

    def _do_resync_sidecar(self, sc_path):
        """A `*.alexandria` sidecar appeared or changed in the library
        from outside the app — reject it rather than import it.

        Alexandria's own sidecar writes are atomic (`sidecar.write`
        does write-tmp + `os.replace`), which the kernel/GIO surface as
        a RENAMED event — and the RENAMED branch in `_on_changed`
        ignores sidecars. So a CREATED / CHANGED / CHANGES_DONE_HINT
        event on a sidecar means an *external* writer: most likely a
        shared sidecar that someone emailed and the recipient saved
        into the library (possibly overwriting their own notes on the
        matching PDF).

        Importing other people's sidecars safely needs a merge / import-
        preview flow that doesn't yet exist (see BACKLOG "Sharing"), and
        a blind upsert here would clobber the recipient's own notes. So
        we make no change to the index and surface a toast. The user's
        on-disk file is left as-is; nothing in Alexandria has acted on
        it."""
        if not os.path.isfile(sc_path):
            return
        if not sc_path.endswith(sidecar.SIDECAR_SUFFIX):
            return
        if self.on_sidecar_rejected:
            GLib.idle_add(self.on_sidecar_rejected,
                          os.path.basename(sc_path))
            return
        if self.on_change:
            GLib.idle_add(self.on_change, "sidecar")
