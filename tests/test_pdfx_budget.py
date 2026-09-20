"""pdfx cannot be allowed to stop the library importing.

Reported 2026-09-20: "Add to Archive" on an author's page downloaded a
paper (6.4 MB, 44 pages, Nature Communications, iText-produced) and the
main window never showed it. The file was on disk with no sidecar, no
thumbnail and no index row.

It was not failing — it was still going. `pdfx.PDFx()` parses the whole
document to build a reference list we never read, and on that file it
ran **over five minutes at 100% CPU** without finishing. Because
extraction is serialised behind `_extract_gate`, that does not merely
import one paper slowly: it holds up every other import, including the
watcher's, which is what "nothing happens" looked like from outside.

Two measurements shaped the fix. **XMP is the only thing we take from
pdfx**, and that file has no XMP stream at all — so all the work could
never have produced anything. And checking for XMP with pypdf costs
13–21 ms, against 0.5–3.1 s for pdfx on normal papers.
"""

import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract


class _Done:
    def __init__(self, stdout=b"", returncode=0):
        self.stdout = stdout
        self.returncode = returncode
        self.stderr = b""


SUMMARY = {"source": {}, "metadata": {"dc": {"title": "A paper"}},
           "references": []}


def test_no_xmp_means_pdfx_is_never_run(monkeypatch):
    """The case from the report. The document cannot yield what we
    would be asking for, so asking costs minutes for nothing."""
    monkeypatch.setattr(extract, "HAVE_PDFX", True)
    monkeypatch.setattr(extract, "_has_xmp", lambda _p: False)
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: pytest.fail("pdfx must not be started"))

    assert extract._run_pdfx("/x/paper.pdf") is None


def test_with_xmp_it_runs_and_returns_the_summary(monkeypatch):
    monkeypatch.setattr(extract, "HAVE_PDFX", True)
    monkeypatch.setattr(extract, "_has_xmp", lambda _p: True)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["timeout"] = kwargs.get("timeout")
        return _Done(json.dumps(SUMMARY).encode())

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert extract._run_pdfx("/x/paper.pdf") == SUMMARY
    # In a child process, because a Python thread cannot be stopped
    # and this one has to be stoppable.
    assert seen["cmd"][0] == sys.executable
    assert seen["cmd"][-1] == "/x/paper.pdf"
    assert seen["timeout"] == extract._PDFX_BUDGET_S


def test_a_slow_file_is_abandoned_not_waited_for(monkeypatch, capsys):
    monkeypatch.setattr(extract, "HAVE_PDFX", True)
    monkeypatch.setattr(extract, "_has_xmp", lambda _p: True)

    def timeout(*_a, **_k):
        raise subprocess.TimeoutExpired(cmd="pdfx", timeout=25)

    monkeypatch.setattr(subprocess, "run", timeout)

    assert extract._run_pdfx("/x/slow.pdf") is None
    # Says so once, naming the file: a paper that imports without its
    # XMP should not do so silently.
    assert "slow.pdf" in capsys.readouterr().out


@pytest.mark.parametrize("done", [
    _Done(b"", returncode=1),          # child failed
    _Done(b"not json"),                # child printed rubbish
    _Done(b'"a string"'),              # valid JSON, wrong shape
    _Done(b""),                        # nothing at all
])
def test_a_bad_answer_is_no_answer(monkeypatch, done):
    monkeypatch.setattr(extract, "HAVE_PDFX", True)
    monkeypatch.setattr(extract, "_has_xmp", lambda _p: True)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: done)

    assert extract._run_pdfx("/x/paper.pdf") is None


def test_without_the_library_nothing_is_attempted(monkeypatch):
    monkeypatch.setattr(extract, "HAVE_PDFX", False)
    monkeypatch.setattr(
        extract, "_has_xmp",
        lambda _p: pytest.fail("no need to look at the file"))

    assert extract._run_pdfx("/x/paper.pdf") is None


def test_extraction_still_works_when_pdfx_gives_nothing(monkeypatch,
                                                        tmp_path):
    """The fallback that makes all of the above safe: pypdf's /Info
    dict, which is where the title comes from for a file with no
    XMP."""
    monkeypatch.setattr(extract, "_run_pdfx", lambda *a, **k: None)
    monkeypatch.setattr(extract, "_enrich",
                        lambda result, path, on_progress=None: result)

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 not really a pdf")

    out = extract.extract_from_pdf(str(pdf))

    assert out["title"] is None       # unreadable, but no exception
    assert out["doi"] is None


def test_the_xmp_check_reads_the_file_not_the_whole_document():
    """Guards the claim the gate rests on: this is cheap. A real PDF
    with no XMP answers False, and fast."""
    import time

    sample = os.path.join(HERE, "data")
    candidates = []
    if os.path.isdir(sample):
        candidates = [os.path.join(sample, f) for f in os.listdir(sample)
                      if f.lower().endswith(".pdf")]
    if not candidates:
        pytest.skip("no sample PDF in tests/data")
    t0 = time.time()
    extract._has_xmp(candidates[0])
    assert time.time() - t0 < 2.0
