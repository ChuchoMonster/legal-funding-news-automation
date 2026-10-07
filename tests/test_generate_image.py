"""generate_image.py: the Kie.ai request, polling, and the prompt rules."""
import json

import pytest

from conftest import FakeHTTPResponse


@pytest.fixture
def gi(load, monkeypatch):
    module = load("generate_image.py")
    monkeypatch.setattr(module.time, "sleep", lambda s: None)
    return module


def test_create_task_payload_and_auth(gi, monkeypatch):
    seen = {}

    def post(url, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json)
        return FakeHTTPResponse({"code": 200, "data": {"taskId": "task-1"}})

    monkeypatch.setattr(gi.requests, "post", post)
    assert gi.create_image_task("A courthouse at dawn") == "task-1"
    assert seen["url"] == "https://api.kie.ai/api/v1/jobs/createTask"
    assert seen["headers"]["Authorization"] == "Bearer test-kie-key"
    assert seen["payload"]["model"] == "nano-banana-2"
    assert seen["payload"]["input"]["prompt"] == "A courthouse at dawn"
    assert seen["payload"]["input"]["aspect_ratio"] == "16:9"
    assert seen["payload"]["input"]["output_format"] == "jpg"


def test_create_task_surfaces_api_error(gi, monkeypatch):
    monkeypatch.setattr(gi.requests, "post",
                        lambda *a, **k: FakeHTTPResponse({"code": 402, "msg": "Insufficient credits"}))
    with pytest.raises(RuntimeError, match="Insufficient credits"):
        gi.create_image_task("p")


def scripted_status(gi, monkeypatch, *states):
    calls = []

    def get(url, headers, params, timeout):
        calls.append(params)
        return FakeHTTPResponse({"data": states[min(len(calls), len(states)) - 1]})

    monkeypatch.setattr(gi.requests, "get", get)
    return calls


def test_poll_waits_until_success_and_returns_first_url(gi, monkeypatch):
    calls = scripted_status(gi, monkeypatch, {"state": "waiting"}, {"state": "generating"},
                            {"state": "success", "resultJson": json.dumps(
                                {"resultUrls": ["https://cdn.example.test/1.jpg", "https://cdn.example.test/2.jpg"]})})
    assert gi.poll_for_result("task-1") == "https://cdn.example.test/1.jpg"
    assert calls == [{"taskId": "task-1"}] * 3


@pytest.mark.parametrize("state, error", [
    ({"state": "fail", "failMsg": "content policy"}, "content policy"),
    ({"state": "success", "resultJson": "{}"}, "no image URL"),
])
def test_poll_raises_on_failure_or_missing_url(gi, monkeypatch, state, error):
    scripted_status(gi, monkeypatch, state)
    with pytest.raises(RuntimeError, match=error):
        gi.poll_for_result("task-1")


def test_poll_times_out(gi, monkeypatch):
    calls = scripted_status(gi, monkeypatch, {"state": "waiting"})
    with pytest.raises(TimeoutError):
        gi.poll_for_result("task-1", max_wait=20, interval=5)
    assert len(calls) == 4


@pytest.mark.parametrize("style, expected", [("watercolor", "watercolor"),
                                              ("neon-pop", "corporate")])
def test_prompt_carries_headline_style_and_no_people_rule(gi, monkeypatch, tmp_path, style, expected):
    prompts, downloads = [], []
    monkeypatch.setattr(gi, "create_image_task", lambda p: prompts.append(p) or "t1")
    monkeypatch.setattr(gi, "poll_for_result", lambda task_id: "https://cdn.example.test/i.jpg")
    monkeypatch.setattr(gi, "download_image", lambda url, out: downloads.append((url, out)))

    out = str(tmp_path / "img.jpg")
    assert gi.generate_image("Funder Backs Patent Claim", out, style) == out
    prompt = prompts[0]
    assert "Topic: Funder Backs Patent Claim." in prompt
    assert gi.STYLES[expected] in prompt
    assert "No text or words in the image." in prompt
    assert "Do NOT include any people" in prompt
    assert downloads == [("https://cdn.example.test/i.jpg", out)]
