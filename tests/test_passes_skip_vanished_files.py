"""Background passes skip papers whose files have gone.

Seen 2026-09-25 in the Flatpak: its index held rows for PDFs that no
longer existed, and on every launch the CrossRef backfill made a
network call for each, then failed reading a sidecar that was not
there — logged, wrongly, as "sidecar write failed".

The rows are not a bug. `watcher.reconcile_startup` deliberately does
not delete an index row whose file is missing, because "a temporarily
unmounted share would otherwise wipe the index". That reasoning
stands, so the fix is to *skip* such rows, not to prune them — and to
skip them before spending anything, since the point is that there is
nowhere to put the answer.

Three passes walk the library and all three were doing it: the
CrossRef license/crossmark backfill, the citation refresher, and PDB
indexing (which asks Europe PMC before falling back to the PDF's own
text, so it costs a call too).
"""

import os
import sys
import threading
import types

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import browse, index, metrics, sidecar


# ---- the predicate ---------------------------------------------------

def test_a_row_whose_pdf_is_there_is_backed(tmp_path):
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fake")

    assert browse._row_is_backed({"pdf_path": str(pdf)}) is True


def test_a_row_whose_pdf_is_gone_is_not(tmp_path):
    assert browse._row_is_backed(
        {"pdf_path": str(tmp_path / "gone.pdf")}) is False


def test_a_row_with_no_path_at_all_is_left_alone(tmp_path):
    """A ghost — a BibTeX entry with no PDF yet — is not a vanished
    file, and the passes should still enrich it."""
    assert browse._row_is_backed({"pdf_path": None}) is True
    assert browse._row_is_backed({}) is True


def test_a_missing_sidecar_does_not_count(tmp_path):
    """The check is the PDF. A sidecar missing beside a PDF that is
    still there is a different fault, worth repairing rather than
    skipping."""
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF fake")

    assert browse._row_is_backed(
        {"pdf_path": str(pdf),
         "sidecar_path": str(tmp_path / "nope.alexandria")}) is True


# ---- a library with one paper present and one vanished ---------------

@pytest.fixture
def library(tmp_path):
    """Two rows with DOIs and no license: `here.pdf` on disk,
    `gone.pdf` deleted after indexing, exactly as a user moving their
    library leaves things."""
    db = str(tmp_path / "lib.db")
    conn = index.open_db(db)
    paths = {}
    for name in ("here", "gone"):
        pdf = str(tmp_path / (name + ".pdf"))
        with open(pdf, "wb") as fh:
            fh.write(b"%PDF fake")
        sc = sidecar.sidecar_path_for(pdf)
        rec = sidecar.new_record(pdf)
        rec.update({"title": name, "doi": "10.1107/" + name})
        sidecar.write(sc, rec)
        index.upsert(conn, pdf, sc, None, rec, os.path.getmtime(sc))
        paths[name] = (pdf, sc)
    conn.close()
    # ...and now the folder loses one of them.
    os.unlink(paths["gone"][0])
    os.unlink(paths["gone"][1])
    return types.SimpleNamespace(db=db, paths=paths)


class _Window:
    """The three passes on a stand-in with the handful of attributes
    they touch."""

    _crossref_extras_backfill = browse.BrowserWindow._crossref_extras_backfill
    _pdb_mentions_backfill = browse.BrowserWindow._pdb_mentions_backfill
    _citation_refresher_loop = browse.BrowserWindow._citation_refresher_loop

    def __init__(self, db):
        self._db_path = db
        self._lic_stop = threading.Event()
        self._pdb_stop = threading.Event()
        self._cit_stop = threading.Event()
        self._cit_failed_session = set()
        self.reloads = []

    def request_reload(self, why):
        self.reloads.append(why)


# ---- the CrossRef backfill -------------------------------------------

def test_crossref_asks_only_about_papers_that_are_there(library,
                                                        monkeypatch):
    asked = []
    monkeypatch.setattr(metrics, "fetch_crossref_extras",
                        lambda doi: asked.append(doi) or None)

    _Window(library.db)._crossref_extras_backfill(
        initial_delay_seconds=0, pause_seconds=0)

    assert asked == ["10.1107/here"], "no call for the vanished paper"


def test_crossref_still_fills_the_paper_that_is_there(library, monkeypatch):
    monkeypatch.setattr(
        metrics, "fetch_crossref_extras",
        lambda doi: {"license": {"label": "CC-BY"}, "crossmark": None})

    win = _Window(library.db)
    win._crossref_extras_backfill(initial_delay_seconds=0, pause_seconds=0)

    rec = sidecar.read(library.paths["here"][1])
    assert rec["license"] == {"label": "CC-BY"}
    # The repaint is queued with GLib.idle_add, not called: pump the
    # loop or it never arrives.
    from gi.repository import GLib
    ctx = GLib.MainContext.default()
    while ctx.pending():
        ctx.iteration(False)
    assert win.reloads == ["crossref-backfill"]


def test_a_sidecar_read_failure_says_read(library, monkeypatch, capsys):
    """It said "sidecar write failed" for a read that failed, which
    sends anyone debugging it to the wrong line."""
    monkeypatch.setattr(
        metrics, "fetch_crossref_extras",
        lambda doi: {"license": {"label": "CC-BY"}, "crossmark": None})
    # The PDF is there, so the row is walked; the sidecar is not.
    os.unlink(library.paths["here"][1])

    _Window(library.db)._crossref_extras_backfill(
        initial_delay_seconds=0, pause_seconds=0)

    said = capsys.readouterr().out + capsys.readouterr().err
    assert "sidecar read failed" in said
    assert "sidecar write failed" not in said


# ---- the citation refresher ------------------------------------------

def test_the_citation_refresher_skips_vanished_papers(library, monkeypatch):
    asked = []

    def fake_metrics(doi):
        asked.append(doi)
        return (None,) * 12

    monkeypatch.setattr(metrics, "fetch_metrics", fake_metrics)
    monkeypatch.setattr(metrics, "openalex_paused_until", lambda: 0)
    conn = index.connect_existing(library.db)
    try:
        _Window(library.db)._citation_refresher_loop(
            conn, max_age_days=0, pause_seconds=0)
    finally:
        conn.close()

    assert "10.1107/gone" not in asked


# ---- PDB indexing ----------------------------------------------------

def test_pdb_indexing_skips_vanished_papers(library, monkeypatch):
    asked = []

    def fake_index(conn, paper_id, **_kw):
        row = conn.execute("SELECT pdf_path FROM papers WHERE id=?",
                           (paper_id,)).fetchone()
        asked.append(os.path.basename(row["pdf_path"]))
        return 0

    from alexandria import pdb_mentions
    monkeypatch.setattr(pdb_mentions, "index_pdb_mentions_for_paper",
                        fake_index)

    _Window(library.db)._pdb_mentions_backfill(
        initial_delay_seconds=0, pause_seconds=0)

    assert "gone.pdf" not in asked
    assert "here.pdf" in asked
