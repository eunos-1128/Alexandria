"""A citing-impact walk that was cut short must say so.

Found while writing `docs/design/citing-impact.md`. The citer loop
breaks out when a request comes back empty — a rate limit, the
OpenAlex breaker, a timeout — and whatever had been counted was
returned, cached for thirty days, and shown as though it were the
answer. In the numbers alone a bucket reading zero because nothing
replied is identical to one reading zero because nobody cited.

The real row that exposed it: the author of SHELX, cached as five
software works and **no citing papers at all**.

So the result now carries `complete`, a partial result is never
fresh however recent, and the chip says "(partial)" rather than
"(stale)" — a floor is a more important caveat than an old number.
"""

import datetime
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import index, metrics

AUTHOR = "A5058134267"


def works_page(*ids):
    return {"results": [{"id": "https://openalex.org/" + w,
                         "title": "SHELX: a program for everything",
                         "cited_by_count": 100} for w in ids],
            "meta": {"next_cursor": None}}


def citers_page(n, each=7):
    return {"results": [{"id": "https://openalex.org/WC%d" % i,
                         "cited_by_count": each} for i in range(n)],
            "meta": {"next_cursor": None}}


def serve(pages, monkeypatch):
    """Answer successive calls from `pages`; None means 'failed'."""
    queue = list(pages)

    def fake(url, headers=None, timeout=None, **_k):
        return queue.pop(0) if queue else None

    monkeypatch.setattr(metrics, "_http_get_json", fake)


def test_a_complete_walk_says_so(monkeypatch):
    serve([works_page("W1"), citers_page(3)], monkeypatch)

    out = metrics.compute_citing_impact(AUTHOR, top_n=5)

    assert out["complete"] is True
    assert out["works_truncated"] == 0
    assert out["software"]["total"] == 21


def test_a_walk_cut_short_is_flagged(monkeypatch):
    """The citers request fails outright: the bucket keeps its works
    but holds no citers, which is exactly the shape that lied."""
    serve([works_page("W1"), None], monkeypatch)

    out = metrics.compute_citing_impact(AUTHOR, top_n=5)

    assert out["complete"] is False
    assert out["works_truncated"] == 1
    assert out["software"]["n_works"] == 1
    assert out["software"]["n_citing"] == 0


def test_a_partial_total_is_a_floor_not_a_zero(monkeypatch):
    """Two works: the first answers, the second does not. The total
    is real as far as it goes, and must not be presented as final."""
    serve([works_page("W1", "W2"), citers_page(2), None], monkeypatch)

    out = metrics.compute_citing_impact(AUTHOR, top_n=5)

    assert out["complete"] is False
    assert out["works_truncated"] == 1
    assert out["software"]["total"] == 14, "what did arrive is kept"


def test_an_author_with_no_works_is_complete(monkeypatch):
    serve([{"results": [], "meta": {"next_cursor": None}}], monkeypatch)

    out = metrics.compute_citing_impact(AUTHOR, top_n=5)

    assert out["complete"] is True


# ---- what the cache does with it -------------------------------------

def partial(**over):
    out = {"computed_at": datetime.date.today().isoformat(),
           "complete": False, "works_walked": 3, "works_truncated": 2,
           "software": {"total": 10, "n_citing": 1, "n_works": 2,
                        "mean": 10.0},
           "method": {"total": 0, "n_citing": 0, "n_works": 0,
                      "mean": 0.0},
           "idea": {"total": 0, "n_citing": 0, "n_works": 0,
                    "mean": 0.0}}
    out.update(over)
    return out


def test_the_flag_survives_a_round_trip(tmp_path):
    conn = index.open_db(str(tmp_path / "lib.db"))

    index.set_author_score(conn, AUTHOR, partial())
    assert index.get_author_score(conn, AUTHOR)["complete"] is False

    index.set_author_score(conn, AUTHOR, partial(complete=True))
    assert index.get_author_score(conn, AUTHOR)["complete"] is True


def test_a_partial_row_is_never_fresh(tmp_path):
    """However recent. Thirty days of a floor presented as a total is
    the whole bug."""
    from alexandria import author_works

    conn = index.open_db(str(tmp_path / "lib.db"))
    index.set_author_score(conn, AUTHOR, partial())

    cached = index.get_author_score(conn, AUTHOR)

    assert cached["computed_at"] == datetime.date.today().isoformat()
    assert author_works._author_score_is_fresh(cached) is False


def test_a_complete_row_written_today_is_fresh(tmp_path):
    from alexandria import author_works

    conn = index.open_db(str(tmp_path / "lib.db"))
    index.set_author_score(conn, AUTHOR, partial(
        complete=True,
        software={"total": 10, "n_citing": 1, "n_works": 2, "mean": 10.0}))

    assert author_works._author_score_is_fresh(
        index.get_author_score(conn, AUTHOR)) is True


# ---- rows written before the column existed --------------------------

def test_an_old_row_with_works_but_no_citers_is_distrusted():
    """The SHELX row. `complete` is None — the row cannot say — so the
    shape has to speak: works in a bucket and not one citing paper."""
    from alexandria import author_works

    shelx = {"computed_at": datetime.date.today().isoformat(),
             "complete": None,
             "software": {"total": 0, "n_citing": 0, "n_works": 5,
                          "mean": 0.0},
             "method": {"total": 0, "n_citing": 0, "n_works": 2,
                        "mean": 0.0},
             "idea": {"total": 1434345, "n_citing": 49800,
                      "n_works": 13, "mean": 28.8}}

    assert author_works._looks_truncated(shelx) is True
    assert author_works._author_score_is_fresh(shelx) is False


def test_an_old_row_that_looks_sound_is_still_used():
    """Not every pre-column row is suspect, and recomputing one takes
    minutes."""
    from alexandria import author_works

    sound = {"computed_at": datetime.date.today().isoformat(),
             "complete": None,
             "software": {"total": 95699, "n_citing": 3664, "n_works": 5,
                          "mean": 26.1},
             "method": {"total": 0, "n_citing": 0, "n_works": 0,
                        "mean": 0.0},
             "idea": {"total": 862552, "n_citing": 56672, "n_works": 15,
                      "mean": 15.2}}

    assert author_works._looks_truncated(sound) is False
    assert author_works._author_score_is_fresh(sound) is True
