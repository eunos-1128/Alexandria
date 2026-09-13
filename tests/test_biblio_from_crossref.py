"""Volume, issue and pages on the refresh path.

Found while fixing the SHELXD import. With the DOI finally resolved,
the record still had no volume and no pages, so a citation would read
"Acta Crystallographica Section D (2002)" and stop.

Both sources have the numbers — OpenAlex and CrossRef agree on
58 / 10 / 1772-1779 for `10.1107/s0907444902011678`. Nothing carried
them into the record. `_enrich_from_openalex` runs on the *import*
path only; `_build_record`, which a refresh goes through, read these
three fields from the PDF's own PRISM block and nowhere else. Modern
publisher PDFs stamp PRISM, which is why the gap had never shown up —
this 2002 file has none.

So two small faults: `_crossref_lookup` dropped the fields although
the CrossRef message carries them, and `_build_record` never looked at
what extraction had resolved.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract

MESSAGE = {
    "title": ["Substructure solution with SHELXD"],
    "author": [{"given": "Thomas R.", "family": "Schneider"},
               {"given": "George M.", "family": "Sheldrick"}],
    "container-title": ["Acta Crystallographica Section D"],
    "issued": {"date-parts": [[2002]]},
    "volume": "58", "issue": "10", "page": "1772-1779",
}


class _Response:
    """Enough of urlopen's context manager for `_crossref_lookup`."""

    def __init__(self, payload):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_a):
        return False

    def read(self):
        import json
        return json.dumps(self._payload).encode("utf-8")


def serving(message):
    return lambda *a, **k: _Response({"message": message})


def test_the_lookup_returns_the_biblio_fields(monkeypatch):
    monkeypatch.setattr(extract.urllib.request, "urlopen", serving(MESSAGE))

    out = extract._crossref_lookup("10.1107/s0907444902011678")

    assert (out["volume"], out["issue"], out["pages"]) == (
        "58", "10", "1772-1779")


def test_absent_fields_stay_absent(monkeypatch):
    """A preprint has no volume, and an empty string is not a value."""
    bare = {k: v for k, v in MESSAGE.items()
            if k not in ("volume", "issue", "page")}
    monkeypatch.setattr(extract.urllib.request, "urlopen", serving(bare))

    out = extract._crossref_lookup("10.1101/2024.05.24.595765")

    assert out["volume"] is None
    assert out["issue"] is None
    assert out["pages"] is None


def extracted(**over):
    rec = {"title": "Substructure solution with SHELXD",
           "authors": ["Thomas R. Schneider", "George M. Sheldrick"],
           "year": 2002, "doi": "10.1107/s0907444902011678",
           "journal": "Acta Crystallographica Section D", "raw": {},
           "volume": "58", "issue": "10", "pages": "1772-1779"}
    rec.update(over)
    return rec


def test_the_record_keeps_what_extraction_resolved(monkeypatch, tmp_path):
    """The second half of the fault: `_build_record` read these three
    fields from the PDF's PRISM block alone, so a paper without one
    lost them even after CrossRef had supplied them."""
    from alexandria import importer

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(importer.extract, "extract_from_pdf",
                        lambda _p, **_kw: extracted())
    monkeypatch.setattr(importer.metrics, "biblio_from_raw",
                        lambda _raw: {})

    rec = importer._build_record(str(pdf))

    assert (rec["volume"], rec["issue"], rec["pages"]) == (
        "58", "10", "1772-1779")


def test_prism_still_wins_where_the_pdf_has_it(monkeypatch, tmp_path):
    """Unchanged precedence: the publisher's own stamp is on the paper
    in hand, so it beats a record fetched by DOI."""
    from alexandria import importer

    pdf = tmp_path / "p.pdf"
    pdf.write_bytes(b"%PDF-1.4 x")
    monkeypatch.setattr(importer.extract, "extract_from_pdf",
                        lambda _p, **_kw: extracted())
    monkeypatch.setattr(importer.metrics, "biblio_from_raw",
                        lambda _raw: {"volume": "99", "pages": "1-2"})

    rec = importer._build_record(str(pdf))

    assert rec["volume"] == "99"
    assert rec["pages"] == "1-2"
    assert rec["issue"] == "10"      # PRISM had none; extraction did
