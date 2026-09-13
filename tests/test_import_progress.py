"""Narrating an import, the way `Get PDF` narrates a fetch.

Asked for 2026-09-13. Dropping a PDF into the library raised one
"Importing x…" toast which then sat there: a cold import shells out to
poppler twice and makes up to five network calls, so the toast can
stand unchanged for many seconds with nothing to say whether it is
working or wedged. The toast stays — this is the status line beneath
it.

The callback is deliberately unable to break an import. It belongs to
the GUI, and a paper lost because a status bar raised would be a poor
trade, so `_say` swallows everything.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract, importer, index, sidecar


@pytest.fixture
def library(tmp_path, monkeypatch):
    conn = index.open_db(str(tmp_path / "state" / "library.db"))
    root = tmp_path / "lib"
    root.mkdir()

    monkeypatch.setattr(importer.thumbnail, "make_thumbnail",
                        lambda *a, **k: None)
    monkeypatch.setattr(importer, "_schedule_pdb_indexing", lambda *a: None)
    monkeypatch.setattr(importer, "_enrich_from_openalex",
                        lambda rec, path: rec.update({"citations": 7}))
    monkeypatch.setattr(importer.metrics, "is_preprint_doi", lambda d: False)
    monkeypatch.setattr(importer.jats, "fetch_and_store",
                        lambda path, doi: {"status": "stored"})
    monkeypatch.setattr(
        importer, "_build_record",
        lambda p, on_progress=None: {
            "title": "A paper", "authors": ["A Person"], "year": 2020,
            "journal": "J", "doi": "10.1000/x", "raw": {}})
    return conn, root


def pdf(root, name, body=b"%PDF-1.4 one"):
    p = root / name
    p.write_bytes(body)
    return str(p)


def test_an_import_narrates_its_steps(library):
    conn, root = library
    said = []

    importer.import_pdf(conn, pdf(root, "a.pdf"), on_progress=said.append)

    joined = " | ".join(said)
    assert "Checking whether it is already here" in joined
    assert "DOI 10.1000/x" in joined
    assert "Asking OpenAlex" in joined
    assert "OpenAlex: 7 citations" in joined
    assert "Europe PMC" in joined
    assert "Drawing the thumbnail" in joined


def test_silence_is_the_default(library):
    """Every other caller — the CLI, BibTeX import, the tests — passes
    nothing and must be unaffected."""
    conn, root = library

    rec, status = importer.import_pdf(conn, pdf(root, "b.pdf"))

    assert status == "new"
    assert rec["doi"] == "10.1000/x"


def test_a_duplicate_says_which_file_it_duplicates(library):
    conn, root = library
    first = pdf(root, "first.pdf", b"%PDF-1.4 same")
    importer.import_pdf(conn, first)

    said = []
    importer.import_pdf(conn, pdf(root, "second.pdf", b"%PDF-1.4 same"),
                        on_progress=said.append)

    assert any("first.pdf" in line for line in said), said


def test_a_broken_callback_cannot_break_the_import(library):
    """The whole point of `_say`: the callback belongs to the GUI."""
    conn, root = library

    def hostile(_message):
        raise RuntimeError("status bar on fire")

    rec, status = importer.import_pdf(conn, pdf(root, "c.pdf"),
                                      on_progress=hostile)

    assert status == "new"
    assert rec["title"] == "A paper"


def test_extraction_narrates_the_slow_parts(monkeypatch, tmp_path):
    """The poppler scans and the DOI lookups are where the seconds go."""
    said = []
    monkeypatch.setattr(extract, "_first_page_text", lambda p: "no doi here")
    monkeypatch.setattr(extract, "_is_supplementary", lambda p, t: False)
    monkeypatch.setattr(extract, "_scan_doi_in_pages",
                        lambda p, max_pages=2: None)
    monkeypatch.setattr(extract, "_scrape_doi", lambda t: None)
    monkeypatch.setattr(extract, "_doi_from_filename", lambda p: None)
    monkeypatch.setattr(extract, "doi_from_filename", lambda p: None)
    monkeypatch.setattr(extract, "_doi_by_title",
                        lambda rec, on_progress=None: None)
    monkeypatch.setattr(extract, "_scrape_first_page", lambda p: (None, None))
    monkeypatch.setattr(extract, "drop_pii_artefacts", lambda r, p: r)

    extract._enrich({"title": "T", "authors": [], "year": 2020,
                     "doi": None, "journal": None, "raw": {}},
                    str(tmp_path / "x.pdf"), on_progress=said.append)

    joined = " | ".join(said)
    assert "Reading the first page" in joined
    assert "Looking for a DOI in the text" in joined
    assert "searching by title" in joined


def test_the_watcher_passes_the_file_name_along(monkeypatch, tmp_path):
    """The GUI needs to know *which* file is talking: a multi-file drop
    interleaves several imports on separate threads."""
    from alexandria import watcher as watcher_mod

    seen = []
    w = watcher_mod.LibraryWatcher.__new__(watcher_mod.LibraryWatcher)
    w.on_import_progress = lambda name, message: seen.append((name, message))
    w.root = str(tmp_path)
    w._suppress_path = lambda *a, **k: None
    w.on_change = None

    monkeypatch.setattr(
        watcher_mod.importer, "import_pdf_or_attach",
        lambda conn, path, root, suppress=None, on_progress=None:
            (on_progress("Checking whether it is already here"),
             ({"title": "x"}, "new", None))[1])

    w._do_import_with_conn(None, str(tmp_path / "paper.pdf"))

    assert seen == [("paper.pdf", "Checking whether it is already here")]
