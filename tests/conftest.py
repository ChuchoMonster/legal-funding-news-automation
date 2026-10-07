"""Shared helpers. Several scripts read credentials and exit at import time, so
each test imports a fresh copy with fake values in the environment."""
import importlib.util
import os
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent

FAKE_ENV = {
    "SERPAPI_KEY": "test-serpapi-key",
    "KIE_API_KEY": "test-kie-key",
    "MAILCHIMP_API_KEY": "0123456789abcdef-us9",
    "MAILCHIMP_LIST_ID": "list-abc",
    "MAILCHIMP_SEGMENT_ID": "",
    "NEWSLETTER_FROM_NAME": "Legal Funding Journal",
    "NEWSLETTER_REPLY_TO": "editor@example.com",
    "BUFFER_API_TOKEN": "test-buffer-token",
    "BUFFER_CHANNEL_ID": "channel-lfj",
    "WP_SITE_URL": "https://wp.example.test",
    "WP_USERNAME": "editor",
    "WP_APP_PASSWORD": "app-pass",
    "WP_CAT_CONSUMER": "238",
    "WP_CAT_PUBLIC": "86",
    "WP_TAG_LITIGATION_FUNDING": "12",
    "CONTRIBUTOR_EMAIL": "author@example.com",
    "CONTRIBUTOR_BYLINE": "The following was contributed by a guest author.",
}


@pytest.fixture
def load(monkeypatch):
    """load("search_news.py", argv=[...], **env_overrides) -> a freshly imported module."""
    def _load(filename, argv=None, **overrides):
        for key in list(os.environ):
            if key in FAKE_ENV or key.startswith(("WP_", "MAILCHIMP_", "BUFFER_")):
                monkeypatch.delenv(key, raising=False)
        for key, value in {**FAKE_ENV, **overrides}.items():
            monkeypatch.setenv(key, value)
        monkeypatch.setattr(sys, "argv", argv or [filename])
        name = "lfj_" + filename.replace("-", "_").removesuffix(".py")
        spec = importlib.util.spec_from_file_location(name, ROOT / filename)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


class FakeHTTPResponse:
    """Minimal stand-in for a `requests` response."""

    def __init__(self, payload=None, status=200, content=b""):
        self._payload, self.status_code, self.content = payload, status, content

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")
