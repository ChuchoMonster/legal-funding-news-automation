"""buffer_post.py: the LinkedIn post request sent to Buffer's GraphQL API."""
import io
import json
import sys

import pytest

OK = {"data": {"createPost": {"__typename": "PostActionSuccess",
                              "post": {"id": "p1", "status": "scheduled", "dueAt": "x",
                                       "text": "t", "channelId": "channel-lfj"}}}}


@pytest.fixture
def bp(load):
    return load("buffer_post.py")


def run(bp, monkeypatch, *argv, response=OK):
    sent = []
    monkeypatch.setattr(bp, "gql", lambda token, query, variables: sent.append(
        (token, query, variables)) or response)
    monkeypatch.setattr(sys, "argv", ["buffer_post.py", *argv])
    bp.main()
    return sent[0][2]["in"], sent[0][0]


@pytest.mark.parametrize("at, due", [
    ("2026-01-12 09:00", "2026-01-12T14:00:00.000Z"),   # EST
    ("2026-06-23 09:00", "2026-06-23T13:00:00.000Z"),   # EDT
])
def test_scheduled_post_converts_eastern_time_to_utc(bp, monkeypatch, at, due):
    post, token = run(bp, monkeypatch, "--text", "Funding news", "--at", at)
    assert token == "test-buffer-token"
    assert post == {"channelId": "channel-lfj", "text": "Funding news",
                    "schedulingType": "automatic", "assets": [], "saveToDraft": False,
                    "mode": "customScheduled", "dueAt": due}


def test_draft_now_queue_and_image_modes(bp, monkeypatch, tmp_path):
    post, _ = run(bp, monkeypatch, "--text", "x", "--at", "2026-06-23 09:00", "--draft")
    assert post["saveToDraft"] is True

    post, _ = run(bp, monkeypatch, "--text", "x", "--now")
    assert post["mode"] == "shareNow" and "dueAt" not in post

    text_file = tmp_path / "post.txt"
    text_file.write_text("  A funder closed a new fund.\n\n")
    post, _ = run(bp, monkeypatch, "--text-file", str(text_file), "--queue",
                  "--image-url", "https://wp.example.test/featured.jpg")
    assert post["mode"] == "addToQueue"
    assert post["text"] == "A funder closed a new fund."
    assert post["assets"] == [{"image": {"url": "https://wp.example.test/featured.jpg"}}]


@pytest.mark.parametrize("response", [
    {"errors": [{"message": "Unauthorized"}]},
    {"data": {"createPost": {"__typename": "InvalidInputError", "message": "dueAt in the past"}}},
])
def test_buffer_failures_exit_nonzero(bp, monkeypatch, response):
    with pytest.raises(SystemExit) as exc:
        run(bp, monkeypatch, "--text", "x", "--now", response=response)
    assert exc.value.code == 1


def test_empty_text_and_missing_channel_are_refused(load, monkeypatch, tmp_path):
    bp = load("buffer_post.py")
    empty = tmp_path / "empty.txt"
    empty.write_text("   \n")
    with pytest.raises(SystemExit, match="Empty post text"):
        run(bp, monkeypatch, "--text-file", str(empty), "--now")

    no_channel = load("buffer_post.py", BUFFER_CHANNEL_ID="")
    with pytest.raises(SystemExit, match="BUFFER_CHANNEL_ID not set"):
        run(no_channel, monkeypatch, "--text", "x", "--now")


def test_gql_sends_bearer_authenticated_json(bp, monkeypatch):
    seen = {}

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def urlopen(req):
        seen["req"] = req
        return Response(b'{"data": {}}')

    monkeypatch.setattr(bp.urllib.request, "urlopen", urlopen)
    assert bp.gql("tok", "mutation M { x }", {"in": {"text": "hi"}}) == {"data": {}}
    req = seen["req"]
    assert req.full_url == "https://api.buffer.com" and req.get_method() == "POST"
    assert req.get_header("Authorization") == "Bearer tok"
    assert json.loads(req.data) == {"query": "mutation M { x }", "variables": {"in": {"text": "hi"}}}
