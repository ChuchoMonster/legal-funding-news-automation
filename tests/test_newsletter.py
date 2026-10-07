"""send-newsletter.py: markdown parsing, HTML assembly and the Mailchimp calls."""
import re
import sys
from datetime import datetime

import pytest

from conftest import ROOT, FakeHTTPResponse

SAMPLE = ROOT / "examples" / "sample-newsletter.md"

TEMPLATE = """<html><body><table><tr>
<td class="mcnTextContent"><p style="x">Intro kept</p>
<p style="x">Hey folks,</p>
<p style="x">Old story one.</p>
<p style="x">Old story two.</p>
</td></tr></table>
<table style="background-color: #404040"><tr><td><h2>What to Watch Next Week</h2></td></tr></table>
<table><tr><td><ul><li>Old bullet</li></ul></td></tr></table>
<p>Footer kept</p></body></html>"""


@pytest.fixture
def nl(load):
    return load("send-newsletter.py")


def test_parse_sample_newsletter(nl):
    parsed = nl.parse_newsletter(str(SAMPLE))
    assert parsed["subject"].startswith("Gen Re Calls for EU-Wide TPLF Regulation")
    assert parsed["paragraphs"][0] == "Hey folks,"
    assert len(parsed["paragraphs"]) == 5
    assert len(parsed["bullets"]) == 3
    assert parsed["bullets"][0].startswith("The social media addiction trial verdict")
    assert not any("What to Watch" in p for p in parsed["paragraphs"])


def test_parse_without_subject_or_watch_section(nl, tmp_path):
    md = tmp_path / "n.md"
    md.write_text("Hey folks,\n\nOne paragraph\nspanning two lines.\n")
    parsed = nl.parse_newsletter(str(md))
    assert parsed == {"subject": "", "paragraphs": ["Hey folks,", "One paragraph\nspanning two lines."],
                      "bullets": []}


def test_markdown_links_become_styled_anchors(nl):
    html = nl.md_links_to_html("See [the ruling](https://example.com/a) and [the deal](https://example.com/b).")
    anchors = re.findall(r'<a href="([^"]+)" rel="noopener noreferrer" target="_blank" style="[^"]+">([^<]+)</a>', html)
    assert anchors == [("https://example.com/a", "the ruling"), ("https://example.com/b", "the deal")]
    assert html.startswith("See ") and html.endswith(".")


def test_paragraphs_and_bullets_html(nl):
    paras = nl.build_paragraphs_html(["First.", "Second [link](https://example.com)."])
    assert paras.count(f'<p style="{nl.P_STYLE}">') == 2
    assert 'href="https://example.com"' in paras

    bullets = nl.build_bullets_html(["One", "Two", "Three"])
    assert bullets.startswith("<ul>") and bullets.endswith("</ul>")
    assert bullets.count("<li ") == 3
    assert bullets.count("<br>") == 2                 # spacer between items, not after the last
    assert "Three<br>" not in bullets


def test_replace_article_content_swaps_only_the_article_block(nl):
    out = nl.replace_article_content(TEMPLATE, "<p>NEW ARTICLE</p>\n")
    assert "Old story one." not in out and "Old story two." not in out
    assert "<p>NEW ARTICLE</p>" in out
    assert "Intro kept" in out and "Footer kept" in out and "Old bullet" in out
    assert out.index("NEW ARTICLE") < out.index("</td>") < out.index("#404040")


@pytest.mark.parametrize("broken, message", [
    (TEMPLATE.replace("Hey folks,", "Hi all,"), "Hey folks"),
    (TEMPLATE.replace("#404040", "#ffffff"), "What to Watch"),
])
def test_replace_article_content_refuses_an_unfamiliar_template(nl, broken, message):
    with pytest.raises(RuntimeError, match=message):
        nl.replace_article_content(broken, "<p>x</p>")


def test_replace_bullets_targets_the_list_after_the_heading(nl):
    html = "<ul><li>Navigation</li></ul>" + TEMPLATE
    out = nl.replace_bullets_content(html, "<ul><li>New bullet</li></ul>")
    assert "<li>Navigation</li>" in out
    assert "Old bullet" not in out and "New bullet" in out
    with pytest.raises(RuntimeError, match="What to Watch Next Week"):
        nl.replace_bullets_content("<ul></ul>", "<ul></ul>")


def frozen(nl, monkeypatch, when):
    real = nl.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return when.replace(tzinfo=tz) if tz else when

    monkeypatch.setattr(nl, "datetime", Frozen)


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 7, 15, 14), "2026-07-17T13:00:00+00:00"),   # Wed in summer -> Fri 9am EDT
    (datetime(2026, 7, 17, 14), "2026-07-24T13:00:00+00:00"),   # on a Friday -> next week
    (datetime(2026, 1, 13, 14), "2026-01-16T14:00:00+00:00"),   # winter -> Fri 9am EST
])
def test_next_friday_9am_et(nl, monkeypatch, now, expected):
    frozen(nl, monkeypatch, now)
    assert nl.next_friday_9am_et() == expected


class FakeMailchimp:
    def __init__(self):
        self.calls = []

    def _record(self, method, url, json=None):
        self.calls.append((method, url, json))
        path = url.split("/3.0", 1)[1]
        if method == "GET" and path.startswith("/campaigns?"):
            return FakeHTTPResponse({"campaigns": [
                {"id": "draft-1", "status": "save", "settings": {"subject_line": "Unsent"}},
                {"id": "sent-7", "status": "sent", "settings": {"subject_line": "Last week"}},
            ]})
        if method == "GET" and path == "/campaigns/sent-7/content":
            return FakeHTTPResponse({"html": TEMPLATE})
        if method == "GET" and path.endswith("/send-checklist"):
            return FakeHTTPResponse({"items": [{"is_ready": True, "heading": "Subject"}]})
        if method == "POST" and path == "/campaigns":
            return FakeHTTPResponse({"id": "new-42"})
        return FakeHTTPResponse({})

    def get(self, url, auth, timeout):
        return self._record("GET", url)

    def post(self, url, auth, json, timeout):
        return self._record("POST", url, json)

    def put(self, url, auth, json, timeout):
        return self._record("PUT", url, json)

    def find(self, method, suffix):
        return [c for c in self.calls if c[0] == method and c[1].endswith(suffix)]


def run_main(nl, monkeypatch, *args):
    api = FakeMailchimp()
    monkeypatch.setattr(nl.requests, "get", api.get)
    monkeypatch.setattr(nl.requests, "post", api.post)
    monkeypatch.setattr(nl.requests, "put", api.put)
    monkeypatch.setattr(sys, "argv", ["send-newsletter.py", str(SAMPLE), *args])
    nl.main()
    return api


def test_main_builds_campaign_from_latest_sent_issue(nl, monkeypatch):
    api = run_main(nl, monkeypatch)
    assert nl.BASE_URL == "https://us9.api.mailchimp.com/3.0"   # data centre from the key

    (_, url, campaign), = api.find("POST", "/campaigns")
    assert campaign["recipients"] == {"list_id": "list-abc"}
    assert campaign["settings"]["reply_to"] == "editor@example.com"
    assert campaign["settings"]["subject_line"].startswith("Gen Re Calls")
    assert campaign["settings"]["preview_text"] == "Hey folks,"
    assert api.find("GET", "/campaigns/sent-7/content")              # skipped the unsent draft

    (_, _, content), = api.find("PUT", "/campaigns/new-42/content")
    html = content["html"]
    assert "Old story one." not in html and "Old bullet" not in html
    assert "unified regulatory framework for third-party litigation funding</a>" in html
    assert "California&#x27;s" not in html and "California's proposed" in html
    assert api.find("POST", "/actions/schedule") == []                  # draft only by default


def test_main_with_schedule_and_segment(load, monkeypatch):
    nl = load("send-newsletter.py", MAILCHIMP_SEGMENT_ID="501")
    frozen(nl, monkeypatch, datetime(2026, 7, 16, 14))
    api = run_main(nl, monkeypatch, "--schedule")
    (_, _, campaign), = api.find("POST", "/campaigns")
    assert campaign["recipients"]["segment_opts"] == {"saved_segment_id": 501}
    (_, _, schedule), = api.find("POST", "/campaigns/new-42/actions/schedule")
    assert schedule == {"schedule_time": "2026-07-17T13:00:00+00:00"}


def test_missing_reply_to_stops_at_import(load):
    with pytest.raises(SystemExit):
        load("send-newsletter.py", NEWSLETTER_REPLY_TO="")
