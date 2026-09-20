"""Reading the PDF's own metadata, without pdfx.

pdfx was archived in 2023, last released in April 2021, and pins
`pdfminer.six==20201018` and `chardet==4.0.0` exactly. It was used for
one thing — the XMP packet — and to get it, `PDFx()` parsed every page
to build a reference list nothing here reads. On a 44-page paper that
ran over five minutes at 100% CPU without finishing, and because
extraction is serialised, it stopped the whole library importing.

pypdf already exposes both the XMP packet and the `/Info` dictionary,
so this reads them directly. The shape is deliberately the one pdfx
returned — namespaces keyed by URI, `/Info` flattened alongside —
because the field logic encodes a lot of publisher-specific knowledge
and `metrics.biblio_from_raw` reads the PRISM block out of sidecars
already written to disk.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import extract, metrics

PRISM_3 = "http://prismstandard.org/namespaces/basic/3.0/"
DC = "http://purl.org/dc/elements/1.1/"

XMP = ('<?xpacket begin="" id="W5M0MpCehiHzreSzNTczkc9d"?>'
       '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
       '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
       '<rdf:Description rdf:about=""'
       ' xmlns:dc="http://purl.org/dc/elements/1.1/"'
       ' xmlns:prism="http://prismstandard.org/namespaces/basic/3.0/"'
       ' xmlns:pdfx="http://ns.adobe.com/pdfx/1.3/">'
       '<dc:title><rdf:Alt><rdf:li xml:lang="x-default">'
       'A Paper About Things</rdf:li></rdf:Alt></dc:title>'
       '<dc:creator><rdf:Seq>'
       '<rdf:li>Ada Lovelace</rdf:li><rdf:li>Alan Turing</rdf:li>'
       '</rdf:Seq></dc:creator>'
       '<prism:volume>165</prism:volume>'
       '<prism:number>6</prism:number>'
       '<prism:pageRange>1332-1345</prism:pageRange>'
       '<prism:publicationName>Cell</prism:publicationName>'
       '<pdfx:doi>10.1016/j.cell.2016.05.041</pdfx:doi>'
       '</rdf:Description></rdf:RDF></x:xmpmeta>').encode()

ATTR_XMP = ('<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
            '<rdf:Description rdf:about=""'
            ' xmlns:prism="http://prismstandard.org/namespaces/basic/3.0/"'
            ' prism:volume="42" prism:number="7"/>'
            '</rdf:RDF>').encode()


class _Stream:
    def __init__(self, data):
        self._data = data

    def get_data(self):
        return self._data


class _Xmp:
    def __init__(self, data):
        self.stream = _Stream(data)


def fake_reader(monkeypatch, info=None, xmp=None):
    class Reader:
        def __init__(self, _path):
            self.metadata = info
            self.xmp_metadata = _Xmp(xmp) if xmp is not None else None

    monkeypatch.setattr(extract, "PdfReader", Reader)
    monkeypatch.setattr(extract, "HAVE_PYPDF", True)


def test_namespaces_are_keyed_by_uri_and_by_prefix(monkeypatch):
    """Both conventions resolve: the field readers ask by prefix,
    `metrics.biblio_from_raw` asks by URI."""
    fake_reader(monkeypatch, xmp=XMP)

    md = extract._xmp_metadata("/x/p.pdf")

    assert md[DC]["title"] == "A Paper About Things"
    assert md["dc"]["title"] == "A Paper About Things"
    assert md[PRISM_3]["volume"] == "165"


def test_a_sequence_of_authors_stays_a_sequence(monkeypatch):
    """`dc:creator` is how the author list arrives; keeping only the
    first name would lose the paper's other authors."""
    fake_reader(monkeypatch, xmp=XMP)

    assert extract._xmp_metadata("/x/p.pdf")["dc"]["creator"] == [
        "Ada Lovelace", "Alan Turing"]


def test_properties_written_as_attributes_are_read(monkeypatch):
    """Adobe's writers put properties on rdf:Description as
    attributes; Elsevier's use child elements. Both are XMP."""
    fake_reader(monkeypatch, xmp=ATTR_XMP)

    md = extract._xmp_metadata("/x/p.pdf")

    assert md[PRISM_3] == {"volume": "42", "number": "7"}


def test_the_info_dictionary_is_flattened_alongside(monkeypatch):
    """Where the title lives for a paper with no XMP — bioRxiv's, for
    instance."""
    fake_reader(monkeypatch, info={"/Title": "From /Info", "/Author": "A N"})

    md = extract._xmp_metadata("/x/p.pdf")

    assert md["Title"] == "From /Info"
    assert md["Author"] == "A N"


def test_info_and_xmp_together(monkeypatch):
    fake_reader(monkeypatch, info={"/Producer": "Acrobat"}, xmp=XMP)

    md = extract._xmp_metadata("/x/p.pdf")

    assert md["Producer"] == "Acrobat"
    assert md["dc"]["title"] == "A Paper About Things"


def test_the_fields_come_out_as_before(monkeypatch):
    fake_reader(monkeypatch, xmp=XMP)

    out = extract._extract_from_xmp("/x/p.pdf")

    assert out["title"] == "A Paper About Things"
    assert out["authors"] == ["Ada Lovelace", "Alan Turing"]
    assert out["doi"] == "10.1016/j.cell.2016.05.041"


def test_the_prism_block_still_reaches_biblio_from_raw(monkeypatch):
    """The sidecar contract: `raw` is stored, and volume/issue/pages
    are read back out of it later."""
    fake_reader(monkeypatch, xmp=XMP)

    out = extract._extract_from_xmp("/x/p.pdf")

    assert metrics.biblio_from_raw(out["raw"]) == {
        "volume": "165", "issue": "6", "pages": "1332-1345"}


def test_no_metadata_at_all_is_none_not_an_error(monkeypatch):
    fake_reader(monkeypatch)

    assert extract._xmp_metadata("/x/p.pdf") == {}
    assert extract._extract_from_xmp("/x/p.pdf") is None


def test_broken_xmp_falls_back_to_the_info_dictionary(monkeypatch):
    """Malformed XMP must not cost the /Info fields as well."""
    fake_reader(monkeypatch, info={"/Title": "Still here"},
                xmp=b"<rdf:RDF not closed")

    md = extract._xmp_metadata("/x/p.pdf")

    assert md["Title"] == "Still here"


def test_an_unreadable_file_is_empty(monkeypatch):
    class Boom:
        def __init__(self, _p):
            raise OSError("not a pdf")

    monkeypatch.setattr(extract, "PdfReader", Boom)
    monkeypatch.setattr(extract, "HAVE_PYPDF", True)

    assert extract._xmp_metadata("/x/p.pdf") == {}


# ---- which slot holds the DOI ---------------------------------------

def fields(**md):
    return extract._fields_from_metadata(md) or {}


def test_a_url_that_is_not_a_doi_is_not_taken_as_one():
    """arXiv writes its landing page into `dc:identifier` and the real
    DOI into `/Info`. Keeping the URL stopped every later lookup,
    because the record then *had* a DOI."""
    out = fields(DOI="https://doi.org/10.48550/arXiv.2506.14430",
                 dc={"identifier": "https://arxiv.org/abs/2506.14430v1",
                     "title": "Works-magnet"})

    assert out["doi"] == "10.48550/arXiv.2506.14430"


def test_no_doi_anywhere_is_none_not_a_stray_url():
    out = fields(dc={"identifier": "https://example.org/paper",
                     "title": "A paper"})

    assert out["doi"] is None


def test_the_info_key_is_matched_whatever_its_case():
    """arXiv spells it /DOI, Elsevier /doi."""
    assert fields(DOI="10.1234/upper")["doi"] == "10.1234/upper"
    assert fields(doi="10.1234/lower")["doi"] == "10.1234/lower"


def test_a_doi_prefixed_identifier_still_works():
    """The common Elsevier form: "doi:10.1016/…" in dc:identifier."""
    out = fields(dc={"identifier": "doi:10.1016/j.cell.2016.05.041"})

    assert out["doi"] == "10.1016/j.cell.2016.05.041"


def test_the_publisher_slots_keep_their_order():
    """prism:doi is trusted over a dc:identifier that also parses."""
    out = fields(**{"http://prismstandard.org/namespaces/basic/3.0/":
                    {"doi": "10.1234/prism"},
                    "dc": {"identifier": "doi:10.5678/dc"}})

    assert out["doi"] == "10.1234/prism"


def test_a_filename_in_the_title_slot_is_refused():
    """Some producers write the file's own name there. A title that is
    a filename is worse than none: no title invites the page-1 scrape
    and the DOI lookup, a bad one does not."""
    assert extract._is_garbage_title("a25349.pdf") is True
    assert extract._is_garbage_title("manuscript.docx") is True
    assert extract._is_garbage_title(
        "Structure of the ribosome") is False
