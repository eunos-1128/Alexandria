"""An OpenAlex 401 must not look like "no such paper".

`_http_get_json` retried 429 and 5xx and returned `None` for every
other HTTPError. `None` is also what callers get when OpenAlex has no
record of a DOI, so an unauthenticated Alexandria would have reported
every paper in the world as unknown: no error, no warning, enrichment
simply doing nothing. The key is only required for some endpoints today
(measured 2026-09-12: works, authors, `cites:` and `group_by` all still
answer 200 unauthenticated), which is precisely why this needed fixing
before it bit — the day OpenAlex turns it on, nothing would have said
so.

So: a distinct exception, a breaker so the library walk stops after one
rejection rather than once per paper, a message that names the remedy,
and release of all of that the moment a key arrives.
"""

import os
import sys
import urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import metrics

OA_URL = "https://api.openalex.org/works/https://doi.org/10.1/x"
HEADERS = {"User-Agent": "test"}


@pytest.fixture(autouse=True)
def clean_session_state(monkeypatch):
    """Every flag here is deliberately session-scoped, so each test
    needs its own session."""
    monkeypatch.setattr(metrics, "_openalex_paused_until", 0.0)
    monkeypatch.setattr(metrics, "_ANNOUNCED_AUTH_FAILURE", False)
    monkeypatch.setattr(metrics, "_ANNOUNCED_UNAUTHENTICATED", False)
    monkeypatch.setattr(metrics, "_REPORTED_DEAD_ENDS", set())
    monkeypatch.setattr(metrics, "_OPENALEX_API_KEY", "")


def http_error(code):
    def raise_it(*_a, **_k):
        raise urllib.error.HTTPError(OA_URL, code, "nope", {}, None)
    return raise_it


def test_401_returns_none_but_trips_the_breaker(monkeypatch):
    calls = []

    def counted(*a, **k):
        calls.append(1)
        http_error(401)()

    monkeypatch.setattr(metrics.urllib.request, "urlopen", counted)

    assert metrics._http_get_json(OA_URL, HEADERS, 5) is None
    # Not retried: retrying an authentication failure cannot help.
    assert len(calls) == 1
    assert metrics.openalex_auth_failed() is True
    assert metrics.openalex_rate_limited() is True

    # And the next call does not even open a socket.
    assert metrics._http_get_json(OA_URL, HEADERS, 5) is None
    assert len(calls) == 1


def test_401_raises_a_distinguishable_exception(monkeypatch):
    monkeypatch.setattr(metrics.urllib.request, "urlopen", http_error(403))

    with pytest.raises(metrics.OpenAlexAuthRequired) as exc:
        metrics._http_get_json(OA_URL, HEADERS, 5, raise_on_quota=True)

    # Callers that only care "OpenAlex won't serve us" catch the base.
    assert isinstance(exc.value, metrics.OpenAlexUnavailable)
    # And it is not mistaken for the quota case, whose advice differs.
    assert not isinstance(exc.value, metrics.OpenAlexQuotaExhausted)
    assert "Preferences" in str(exc.value)


def test_the_blocked_reason_names_the_right_remedy(monkeypatch):
    assert metrics.openalex_blocked_reason() is None

    monkeypatch.setattr(metrics.urllib.request, "urlopen", http_error(401))
    metrics._http_get_json(OA_URL, HEADERS, 5)

    reason = metrics.openalex_blocked_reason()
    assert "API key" in reason
    # Waiting for midnight is useless advice for a missing key.
    assert "00:00" not in reason


def test_a_quota_trip_still_reads_as_quota(monkeypatch):
    """The breaker is shared; only the message should differ."""
    metrics._trip_openalex_breaker(120)

    reason = metrics.openalex_blocked_reason()
    assert "quota" in reason
    assert metrics.openalex_auth_failed() is False

    with pytest.raises(metrics.OpenAlexQuotaExhausted):
        metrics._http_get_json(OA_URL, HEADERS, 5, raise_on_quota=True)


def test_a_key_arriving_releases_the_breaker(monkeypatch):
    monkeypatch.setattr(metrics.urllib.request, "urlopen", http_error(401))
    metrics._http_get_json(OA_URL, HEADERS, 5)
    assert metrics.openalex_rate_limited() is True

    metrics.set_openalex_api_key("a-new-key")

    # Pasting a key is the one thing that can fix this, so it must not
    # need a restart to take effect.
    assert metrics.openalex_rate_limited() is False
    assert metrics.openalex_auth_failed() is False
    assert metrics.openalex_blocked_reason() is None


def test_a_404_is_ordinary(monkeypatch, capsys):
    """Plenty of DOIs are simply unknown to OpenAlex."""
    monkeypatch.setattr(metrics.urllib.request, "urlopen", http_error(404))

    assert metrics._http_get_json(OA_URL, HEADERS, 5) is None
    assert metrics.openalex_rate_limited() is False
    assert metrics.openalex_auth_failed() is False
    assert "HTTP 404" not in capsys.readouterr().out


def test_an_unusual_dead_end_is_reported_once(monkeypatch, capsys):
    monkeypatch.setattr(metrics.urllib.request, "urlopen", http_error(400))

    for _ in range(3):
        metrics._http_get_json(OA_URL, HEADERS, 5)

    out = capsys.readouterr().out
    assert out.count("HTTP 400") == 1


def test_unauthenticated_says_so_once(monkeypatch, capsys):
    """A per-call line would be a storm: the citation refresher walks
    the whole library."""
    monkeypatch.setattr(metrics, "OPENALEX_MAILTO", "")
    for _ in range(3):
        metrics._apply_openalex_key(OA_URL)

    out = capsys.readouterr().out
    assert out.count("no OpenAlex API key") == 1
    assert out.count("polite pool") == 1


def test_a_configured_key_is_silent_and_still_applied(monkeypatch, capsys):
    monkeypatch.setattr(metrics, "_OPENALEX_API_KEY", "sekrit")

    url = metrics._apply_openalex_key(OA_URL)

    assert "api_key=sekrit" in url
    assert capsys.readouterr().out == ""
