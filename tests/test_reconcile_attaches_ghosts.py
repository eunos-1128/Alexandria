"""A PDF that duplicates a ghost must be attached by *any* import path.

Reported 2026-09-07. Discover → DOI (creating a BibTeX ghost) → let
the browser extension drop the PDF into the library. The PDF was
never imported: no sidecar, no thumbnail, no index row, only the
ghost. Restarting did not fix it, and could not.

Two faults. The watcher missed the filesystem event, which kqueue
does under load — and the safety net did not cover the case, which is
the real bug: the ghost-merge dispatch lived *only* in
`watcher._do_import_with_conn`. `importer.import_tree`, which
`reconcile_startup` runs, called `import_pdf`, saw "duplicate", wrote
it down and moved on. So every restart re-reported the same duplicate
and changed nothing, and the file sat beside its own ghost for good.

Moving the dispatch into the shared path fixes the second fault and
makes the first self-healing: a dropped event now heals at the next
reconcile.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import bibtex_import, importer, index, sidecar

DOI = "10.1093/nar/25.24.4876"
KEY = "thompson1997the"


@pytest.fixture
def library(tmp_path, monkeypatch):
    """A ghost, and the PDF for it sitting unimported in the root —
    exactly the state the report describes."""
    root = str(tmp_path)
    conn = index.open_db(os.path.join(root, "db.sqlite3"))

    ghost = sidecar.new_record(sidecar.ghost_pdf_path(KEY))
    ghost.update({"title": "The CLUSTAL_X windows interface",
                  "authors": ["Julie Thompson"], "year": "1997",
                  "doi": DOI, "bibtex_key": KEY})
    g_sc = sidecar.ghost_sidecar_path(root, KEY)
    os.makedirs(os.path.dirname(g_sc), exist_ok=True)
    sidecar.write(g_sc, ghost)
    index.upsert(conn, sidecar.ghost_pdf_path(KEY), g_sc, None,
                 ghost, os.path.getmtime(g_sc))

    src = os.path.join(root, "25-24-4876.pdf")
    with open(src, "wb") as fh:
        fh.write(b"%PDF-1.4 junk")

    def fake_import(conn_, pdf_path):
        """What import_pdf does here: the DOI resolves to a paper the
        index already has — the ghost — so it reports a duplicate."""
        if os.path.abspath(pdf_path) == os.path.abspath(src):
            row = conn_.execute(
                "SELECT * FROM papers WHERE pdf_path = ?",
                (sidecar.ghost_pdf_path(KEY),)).fetchone()
            if row is not None:
                return dict(row), "duplicate"
        rec = sidecar.new_record(pdf_path)
        rec.update({"title": "The CLUSTAL_X windows interface",
                    "doi": DOI})
        sc = sidecar.sidecar_path_for(pdf_path)
        sidecar.write(sc, rec)
        index.upsert(conn_, pdf_path, sc, None, rec,
                     os.path.getmtime(sc))
        return rec, "new"

    monkeypatch.setattr(importer, "import_pdf", fake_import)
    monkeypatch.setattr(bibtex_import, "_ghost_doi_check",
                        lambda ghost_doi, src_path: (True, ""))
    return conn, root, src


def _ghost_rows(conn):
    return [r["pdf_path"] for r in conn.execute(
        "SELECT pdf_path FROM papers")
        if sidecar.is_ghost_path(r["pdf_path"])]


def test_reconcile_attaches_a_pdf_the_watcher_missed(library):
    """The bug: this was a no-op, forever."""
    conn, root, src = library

    importer.import_tree(conn, root)

    assert _ghost_rows(conn) == [], "the ghost should have been merged"
    merged = os.path.join(root, KEY + ".pdf")
    assert os.path.isfile(merged), "the PDF should have been attached"
    assert os.path.isfile(sidecar.sidecar_path_for(merged))


def test_the_orphaned_source_is_not_left_beside_its_own_copy(library):
    """attach copies source → <key>.pdf; leaving the source would put
    two copies of one paper in the library."""
    conn, root, src = library

    importer.import_tree(conn, root)

    assert not os.path.exists(src)


def test_a_second_reconcile_changes_nothing(library):
    """Idempotence — reconcile runs at every startup."""
    conn, root, src = library
    importer.import_tree(conn, root)
    before = sorted(os.listdir(root))

    importer.import_tree(conn, root)

    assert sorted(os.listdir(root)) == before
    assert _ghost_rows(conn) == []


def test_a_plain_duplicate_is_still_just_a_duplicate(library, monkeypatch):
    """Only a duplicate *of a ghost* means attach. A real
    double-import must not be rewritten."""
    conn, root, src = library
    real = os.path.join(root, "already-here.pdf")
    with open(real, "wb") as fh:
        fh.write(b"%PDF-1.4 other")
    rec = sidecar.new_record(real)
    rec["title"] = "Something else"
    sc = sidecar.sidecar_path_for(real)
    sidecar.write(sc, rec)
    index.upsert(conn, real, sc, None, rec, os.path.getmtime(sc))

    monkeypatch.setattr(importer, "import_pdf",
                        lambda c, p: (dict(rec), "duplicate"))

    out_rec, status, new_path = importer.import_pdf_or_attach(
        conn, real, root)

    assert status == "duplicate"
    assert new_path is None
    assert os.path.isfile(real)


def test_the_watcher_gets_a_chance_to_suppress_the_new_path(library):
    """The merge creates <key>.pdf; a watcher told about it after the
    fact would import its own output and roll the copy back."""
    conn, root, src = library
    told = []

    importer.import_pdf_or_attach(
        conn, src, root, suppress=lambda p, secs: told.append((p, secs)))

    assert told, "the predicted path was never offered for suppression"
    assert os.path.basename(told[0][0]) == KEY + ".pdf"
