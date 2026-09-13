"""A first run leaves a config file behind.

Measured 2026-09-13 on a genuine first run — empty XDG directories, no
key, no address: Alexandria starts, watches the library, imports,
fetches JATS and enriches four papers, and never writes
`config.json` at all. `load()` defaults everything in memory and only
saves on the first explicit change.

Nothing is broken by that, and that is the trouble: the two settings
that change what the app can do are invisible. Without
`contact_email` Unpaywall refuses to answer, so **Get PDF** silently
consults one source fewer; without `openalex_api_key` every request
comes out of the shared pool. A user who has never opened Preferences
has no file to find them in.
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import pytest

from alexandria import prefs


def test_it_is_created_when_missing(tmp_path):
    path = str(tmp_path / "Alexandria" / "config.json")

    assert prefs.ensure_config_file(path) is True
    assert os.path.isfile(path)

    with open(path) as fh:
        data = json.load(fh)
    # The keys *are* the documentation: JSON has no comments, so an
    # omitted setting is an undiscoverable one.
    assert data["contact_email"] == ""
    assert data["openalex_api_key"] == ""
    assert data["current_catalogue"] == "default"
    assert data["catalogues"][0]["name"] == "default"
    assert data["catalogues"][0]["library_root"]


def test_an_existing_file_is_never_touched(tmp_path):
    path = str(tmp_path / "config.json")
    mine = {"contact_email": "me@example.org", "sort_key": "year"}
    prefs.save(mine, path)

    assert prefs.ensure_config_file(path) is False

    with open(path) as fh:
        assert json.load(fh) == mine


def test_even_a_broken_file_is_left_alone(tmp_path):
    """Someone else's config is not ours to repair behind their back —
    and a file we silently replaced would take their settings with it."""
    path = str(tmp_path / "config.json")
    with open(path, "w") as fh:
        fh.write("{ this is not json")

    assert prefs.ensure_config_file(path) is False
    with open(path) as fh:
        assert fh.read() == "{ this is not json"


def test_what_it_writes_round_trips(tmp_path):
    path = str(tmp_path / "config.json")
    prefs.ensure_config_file(path)

    assert prefs.load(path)["contact_email"] == ""
    # An empty key must read as "unset", not as a key of "".
    assert prefs.get_contact_email(path) == ""
    prefs.save(dict(prefs.load(path), contact_email="me@example.org"), path)
    assert prefs.get_contact_email(path) == "me@example.org"


def test_an_unwritable_location_is_not_fatal(tmp_path, monkeypatch):
    """A starter config is a courtesy. Failing to write one must not
    stop the app from starting."""
    def boom(*_a, **_k):
        raise OSError("read-only file system")

    monkeypatch.setattr(prefs, "save", boom)

    assert prefs.ensure_config_file(str(tmp_path / "config.json")) is False


def test_startup_writes_one(tmp_path, monkeypatch):
    """The call belongs before anything reads preferences."""
    import inspect
    from alexandria import browse

    src = inspect.getsource(browse.main)
    assert "prefs.ensure_config_file()" in src
    assert (src.index("prefs.ensure_config_file()")
            < src.index("prefs.get_openalex_api_key()"))
