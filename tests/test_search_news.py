"""search_news.py (SerpAPI): freshness filter, dedupe, curl handling and retries."""
import json
import subprocess
import types
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def sn(load, monkeypatch):
    module = load("search_news.py")
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    return module


def mmddyyyy(days_ago):
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%m/%d/%Y, 08:00 AM, +0000 UTC")


# --- the 48-hour freshness rule ---------------------------------------------------

@pytest.mark.parametrize("date, recent", [
    ("5 hours ago", True), ("yesterday", True),
    ("2 days ago", True), ("3 days ago", False),
    ("", True),                 # no date: keep it and let the editor judge
    ("Sometime soon", True),    # unknown format: keep it
])
def test_relative_dates(sn, date, recent):
    assert sn.is_recent(date) is recent


def test_absolute_dates(sn):
    assert sn.is_recent(mmddyyyy(0)) is True
    assert sn.is_recent(mmddyyyy(10)) is False
    assert sn.is_recent("13/45/2026, 08:00 AM") is True   # unparseable date is kept


def test_deduplicate_drops_repeats_own_site_and_stale_items(sn):
    articles = [
        {"link": "https://news.example.com/a", "date": "3 hours ago", "title": "A"},
        {"link": "https://news.example.com/a", "date": "4 hours ago", "title": "A again"},
        {"link": "https://legalfundingjournal.com/our-own-story/", "date": "1 hour ago"},
        {"link": "https://news.example.com/old", "date": "6 days ago"},
        {"link": "https://news.example.com/b", "date": "1 day ago", "title": "B"},
    ]
    assert [a["title"] for a in sn.deduplicate(articles)] == ["A", "B"]


# --- curl transport -------------------------------------------------------------

def fake_curl(monkeypatch, sn, stdout="", returncode=0, stderr=""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return types.SimpleNamespace(stdout=stdout, returncode=returncode, stderr=stderr)

    monkeypatch.setattr(sn.subprocess, "run", run)
    return calls


def test_fetch_json_parses_body_and_encodes_params(sn, monkeypatch):
    calls = fake_curl(monkeypatch, sn, stdout='{"news_results": []}\n200')
    assert sn.fetch_json("https://serpapi.test/search.json", {"q": "litigation finance"}, 60) == {
        "news_results": []}
    cmd = calls[0]
    assert cmd[:4] == ["curl", "-s", "--max-time", "60"]
    assert cmd[-1] == "https://serpapi.test/search.json?q=litigation+finance"


@pytest.mark.parametrize("stdout, returncode, message", [
    ('{"error": "slow down"}\n429', 0, "HTTP 429 rate limit"),
    ("<html>Bad gateway</html>\n502", 0, "HTTP 502 server error"),
    ("<html>not json</html>\n200", 0, "HTTP 200, non-JSON response"),
    ("", 28, "timeout"),
    ("", 6, "curl exit 6"),
])
def test_fetch_json_raises_on_transient_failures(sn, monkeypatch, stdout, returncode, message):
    fake_curl(monkeypatch, sn, stdout=stdout, returncode=returncode, stderr="could not resolve")
    with pytest.raises(sn.FetchError, match=message):
        sn.fetch_json("https://serpapi.test", {}, 5)


def test_fetch_json_maps_subprocess_timeout(sn, monkeypatch):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, kw["timeout"])

    monkeypatch.setattr(sn.subprocess, "run", run)
    with pytest.raises(sn.FetchError, match="timeout after 5s"):
        sn.fetch_json("https://serpapi.test", {}, 5)


# --- retries and quota ------------------------------------------------------------

def scripted_fetch(monkeypatch, sn, *outcomes):
    calls = []

    def fetch(url, params, timeout):
        calls.append(params)
        outcome = outcomes[len(calls) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(sn, "fetch_json", fetch)
    return calls


def test_search_retries_transient_errors_then_succeeds(sn, monkeypatch):
    calls = scripted_fetch(monkeypatch, sn, sn.FetchError("timeout"), sn.FetchError("HTTP 503"),
                           {"news_results": [{"link": "https://news.example.com/x"}]})
    results, err = sn.search_google_news("litigation funding")
    assert err is None and results == [{"link": "https://news.example.com/x"}]
    assert len(calls) == 3
    assert "tbs" not in calls[0]          # the time filter that makes SerpAPI hang
    assert calls[0]["tbm"] == "nws"


def test_search_gives_up_after_three_attempts(sn, monkeypatch):
    calls = scripted_fetch(monkeypatch, sn, *[sn.FetchError("HTTP 429 rate limit")] * 3)
    assert sn.search_google_news("legal funding") == ([], "HTTP 429 rate limit")
    assert len(calls) == 3


def test_serpapi_error_field_is_not_retried(sn, monkeypatch):
    calls = scripted_fetch(monkeypatch, sn, {"error": "Your account has run out of searches."})
    results, err = sn.search_google_news("legal funding")
    assert results == [] and err == "SerpAPI error: Your account has run out of searches."
    assert len(calls) == 1


def test_rejected_key_stops_the_run(sn, monkeypatch):
    scripted_fetch(monkeypatch, sn, {"error": "Invalid API key."})
    with pytest.raises(SystemExit) as exc:
        sn.check_quota()
    assert exc.value.code == 2


def test_quota_check_returns_searches_left(sn, monkeypatch, capsys):
    scripted_fetch(monkeypatch, sn, {"total_searches_left": 5, "plan_name": "Dev"})
    assert sn.check_quota() == 5
    assert "fewer searches left (5) than this run needs (8)" in capsys.readouterr().err


# --- the whole run ------------------------------------------------------------

def test_all_queries_failing_is_reported_as_an_outage(sn, monkeypatch, capsys):
    monkeypatch.setattr(sn, "check_quota", lambda: 100)
    monkeypatch.setattr(sn, "search_google_news", lambda q: ([], "HTTP 503 server error"))
    with pytest.raises(SystemExit) as exc:
        sn.main()
    assert exc.value.code == 1
    assert "SerpAPI failure, not an empty news day" in capsys.readouterr().out


def test_partial_failure_still_saves_deduplicated_results(sn, monkeypatch, tmp_path):
    monkeypatch.setattr(sn, "__file__", str(tmp_path / "search_news.py"))
    monkeypatch.setattr(sn, "check_quota", lambda: 100)
    story = {"title": "Funder backs class action", "link": "https://news.example.com/s",
             "date": "2 hours ago", "source": "Example News"}

    def search(query):
        return ([], "timeout") if query == sn.QUERIES[0] else ([story], None)

    monkeypatch.setattr(sn, "search_google_news", search)
    sn.main()
    saved = json.loads((tmp_path / "latest_search_results.json").read_text())
    assert saved == [story]
