"""Europe PMC preprint search.

OpenAlex is late on preprints — often weeks, sometimes never — which
is the whole reason for this source. Europe PMC indexes bioRxiv and
medRxiv directly, needs no key, and one `core` request returns most
of a Discover row.

The query shape is load-bearing and easy to get subtly wrong, so it
is pinned here: `SRC:PPR` selects the preprint corpus, terms are
matched against the abstract rather than the full text, and a quoted
phrase stays one term.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import europepmc

RESULT = {
    "id": "PPR1317919",
    "doi": "10.64898/2026.09.09.750088",
    "title": "Modular Surface Engineering of Feline Parvovirus",
    "firstPublicationDate": "2026-09-11",
    "abstractText": "A" * 1316,
    "authorString": "Naskalska A, Biela A, Rozycki J.",
    "authorList": {"author": [
        {"fullName": "Naskalska A"}, {"fullName": "Biela A"},
        {"fullName": "Rozycki J"}]},
    "bookOrReportDetails": {"publisher": "bioRxiv"},
    "hasPDF": "N", "isOpenAccess": "N",
}


# --- the query ---------------------------------------------------------

def test_the_preprint_corpus_is_selected():
    """Without SRC:PPR the same search returns published papers,
    which OpenAlex already covers better."""
    assert "(SRC:PPR)" in europepmc.build_query("cryo-EM")


def test_terms_match_the_abstract_not_the_full_text():
    """Preprint full text is indexed too, and searching it returns
    papers that mention the words in passing."""
    q = europepmc.build_query("cryo-EM")

    assert 'ABSTRACT:"cryo-EM"' in q
    assert "FULL_TEXT" not in q


def test_a_quoted_phrase_stays_one_term():
    """Otherwise "model building" becomes two unrelated requirements
    and the result set collapses."""
    q = europepmc.build_query('cryo-EM "model building"')

    assert 'ABSTRACT:"model building"' in q
    assert 'ABSTRACT:"model"' not in q


def test_the_date_range_is_bounded_at_both_ends():
    q = europepmc.build_query("x", since="2026-01-01")

    assert "FIRST_PDATE:[2026-01-01 TO 2100-12-31]" in q


def test_no_publisher_means_every_server():
    assert "PUBLISHER" not in europepmc.build_query("x")
    assert 'PUBLISHER:"bioRxiv"' in europepmc.build_query("x", "bioRxiv")


def test_core_results_are_requested():
    """`lite` returns no abstract at all — the difference between a
    stub and a usable row."""
    url = europepmc.search_url("x")

    assert "resultType=core" in url
    assert "P_PDATE_D+desc" in url or "P_PDATE_D%20desc" in url


# --- parsing -----------------------------------------------------------

def test_a_result_becomes_a_discover_row():
    row = europepmc.parse_result(RESULT)

    assert row["doi"] == "10.64898/2026.09.09.750088"
    assert row["first_author"] == "Naskalska A"
    assert row["last_author"] == "Rozycki J"
    assert row["year"] == 2026
    assert row["publication_date"] == "2026-09-11"
    assert len(row["abstract"]) == 1316


def test_the_server_name_stands_in_for_the_journal():
    """Europe PMC files it under bookOrReportDetails, which is an odd
    home for a preprint and the only place it appears."""
    assert europepmc.parse_result(RESULT)["journal"] == "bioRxiv"


def test_a_record_without_a_structured_author_list_still_parses():
    row = europepmc.parse_result(
        dict(RESULT, authorList=None))

    assert row["first_author"] == "Naskalska A"


def test_reachability_flags_are_not_used_to_filter(monkeypatch):
    """Every preprint measured says hasPDF "N" and isOpenAccess "N",
    and every one of them downloads. Filtering on those would discard
    the entire corpus."""
    payload = {"resultList": {"result": [RESULT]}}

    rows = europepmc.search_preprints(
        "x", http_get_json=lambda url: payload)

    assert len(rows) == 1


def test_a_failed_search_is_empty_not_an_exception():
    def boom(url):
        raise OSError("europepmc is down")

    assert europepmc.search_preprints("x", http_get_json=boom) == []


def test_an_empty_response_is_empty():
    assert europepmc.search_preprints(
        "x", http_get_json=lambda url: {}) == []
