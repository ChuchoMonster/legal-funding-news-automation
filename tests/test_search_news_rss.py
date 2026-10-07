"""search_news_rss.py: Google News RSS parsing, window, locales and merging."""
import io
import json
import urllib.parse
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest


def rss(*items):
    body = "".join(
        f"<item><title>{t}</title><link>{link}</link><pubDate>{pub}</pubDate>"
        + (f'<source url="https://src.example">{src}</source>' if src else "")
        + "</item>"
        for t, link, pub, src in items)
    return f'<?xml version="1.0"?><rss><channel>{body}</channel></rss>'.encode()


def hours_ago(h):
    return format_datetime(datetime.now(timezone.utc) - timedelta(hours=h))


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def rss_mod(load):
    return load("search_news_rss.py")


@pytest.mark.parametrize("argv, hours, days", [
    (["search_news_rss.py"], 48, 2),
    (["search_news_rss.py", "96"], 96, 4),     # Monday catch-up
    (["search_news_rss.py", "36"], 36, 2),     # rounds up to whole days
    (["search_news_rss.py", "tests"], 48, 2),  # non-numeric argument is ignored
])
def test_window_comes_from_the_command_line(load, argv, hours, days):
    module = load("search_news_rss.py", argv=argv)
    assert (module.WINDOW_HOURS, module.WINDOW_DAYS) == (hours, days)


def test_fetch_builds_localised_url_and_parses_items(load, monkeypatch):
    module = load("search_news_rss.py", argv=["search_news_rss.py", "96"])
    seen = {}
    feed = rss(("Prozessfinanzierer steigt ein - Handelsblatt", "https://news.example.de/1",
                "Mon, 05 Oct 2026 09:30:00 GMT", "Handelsblatt"),
               ("Untitled wire item", "https://news.example.de/2", "not a date", None))

    def urlopen(req, timeout):
        seen["url"], seen["ua"] = req.full_url, req.get_header("User-agent")
        return FakeResponse(feed)

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    items = module.fetch("Prozessfinanzierer", "DE")

    query = urllib.parse.parse_qs(urllib.parse.urlparse(seen["url"]).query)
    assert query["q"] == ["Prozessfinanzierer when:4d"]
    assert (query["hl"], query["gl"], query["ceid"]) == (["de"], ["DE"], ["DE:de"])
    assert seen["ua"].startswith("Mozilla/5.0")

    first, second = items
    assert first["title"] == "Prozessfinanzierer steigt ein"     # " - Source" suffix removed
    assert first["source"] == "Handelsblatt"
    assert first["dt"] == datetime(2026, 10, 5, 9, 30, tzinfo=timezone.utc)
    assert second["source"] == "" and second["dt"] is None


def test_fetch_failure_returns_empty_list(rss_mod, monkeypatch, capsys):
    def urlopen(req, timeout):
        raise OSError("network unreachable")

    monkeypatch.setattr(rss_mod.urllib.request, "urlopen", urlopen)
    assert rss_mod.fetch('"litigation funding"') == []
    assert "network unreachable" in capsys.readouterr().err


def test_every_localised_query_names_a_known_locale(rss_mod):
    # fetch() silently falls back to US/English for an unknown locale, which is
    # exactly how non-English stories used to go missing.
    for entry in rss_mod.QUERIES:
        if isinstance(entry, tuple):
            assert entry[1] in rss_mod.LOCALES, entry


def test_main_merges_filters_and_sorts_newest_first(rss_mod, monkeypatch, tmp_path):
    monkeypatch.setattr(rss_mod, "__file__", str(tmp_path / "search_news_rss.py"))
    fresh = {"title": "Funder wins appeal", "link": "https://news.example.com/fresh",
             "source": "Example", "pubDate": hours_ago(2),
             "dt": datetime.now(timezone.utc) - timedelta(hours=2)}
    older = {**fresh, "title": "Fund closes", "link": "https://news.example.com/older",
             "dt": datetime.now(timezone.utc) - timedelta(hours=30)}
    undated = {**fresh, "title": "Undated item", "link": "https://news.example.com/undated",
               "dt": None}
    stale = {**fresh, "title": "Old news", "link": "https://news.example.com/stale",
             "dt": datetime.now(timezone.utc) - timedelta(hours=100)}
    ours = {**fresh, "link": "https://legalfundingjournal.com/our-story/"}

    def fetch(query, locale="US"):
        return [older, fresh, undated, stale, ours]   # every query returns the same set

    monkeypatch.setattr(rss_mod, "fetch", fetch)
    assert rss_mod.main() == 0
    saved = json.loads((tmp_path / "latest_rss_results.json").read_text())
    assert [a["title"] for a in saved] == ["Funder wins appeal", "Fund closes", "Undated item"]
    assert all("dt" not in a for a in saved)


def test_main_fails_loudly_when_every_request_errors(rss_mod, monkeypatch, capsys):
    def urlopen(req, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(rss_mod.urllib.request, "urlopen", urlopen)
    assert rss_mod.main() == 2
    assert "tool failure, NOT an empty result" in capsys.readouterr().err
