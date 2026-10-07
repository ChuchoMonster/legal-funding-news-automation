"""post-to-wp.sh, run for real against a fake `curl` on PATH."""
import json
import os
import shutil
import stat
import subprocess

import pytest

from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

FAKE_CURL = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
url = next(a for a in args if a.startswith("http"))
with open(os.environ["CURL_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if url.endswith("/posts"):
    code = os.environ.get("FAKE_CREATE_CODE", "201")
    print(json.dumps({"id": 42, "link": "https://wp.example.test/?p=42"}) if code == "201"
          else json.dumps({"code": "rest_invalid"}))
    print(code, end="")
elif url.endswith("/media"):
    print(json.dumps({"id": 7})); print("201", end="")
else:
    print(json.dumps({"link": "https://wp.example.test/story/"})); print("200", end="")
'''

BLURB = """## Funder Backs $50M Patent Portfolio

A litigation funder has committed $50 million to a patent portfolio, as reported by
[Example News](https://news.example.com/story).

Analysts said the deal signals growing appetite for IP claims.
"""


@pytest.fixture
def wp(tmp_path):
    script = tmp_path / "post-to-wp.sh"
    shutil.copy(ROOT / "post-to-wp.sh", script)
    (tmp_path / ".env").write_text(
        "WP_SITE_URL=https://wp.example.test\nWP_USERNAME=editor\nWP_APP_PASSWORD=pass\n"
        "WP_CAT_PREMIUM=5\nWP_CAT_COMMERCIAL=6\nWP_TAG_LITIGATION_FUNDING=12\n")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(FAKE_CURL)
    curl.chmod(curl.stat().st_mode | stat.S_IEXEC)
    blurb = tmp_path / "blurb.md"
    blurb.write_text(BLURB)
    image = tmp_path / "image.jpg"
    image.write_bytes(b"jpg")
    log = tmp_path / "curl.log"

    def run(*extra_args, **env):
        full_env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
                    "CURL_LOG": str(log), **env}
        result = subprocess.run(["bash", str(script), str(blurb), *extra_args],
                                capture_output=True, text=True, env=full_env, timeout=60)
        calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
        return result, calls

    run.image = image
    return run


def payload(args):
    return json.loads(args[args.index("-d") + 1])


def test_blurb_becomes_a_gutenberg_draft_then_image_then_publish(wp):
    result, calls = wp(str(wp.image))
    assert result.returncode == 0, result.stderr
    create, media, featured, publish = calls

    assert create[create.index("-X") + 1] == "POST"
    assert create[create.index("-u") + 1] == "editor:pass"
    assert payload(create) == {
        "title": "Funder Backs $50M Patent Portfolio",
        "status": "draft",
        "categories": [5, 6],
        "tags": [12],
        "content": ("<!-- wp:paragraph -->\n<p>A litigation funder has committed $50 million to a "
                    "patent portfolio, as reported by <a href=\"https://news.example.com/story\">"
                    "Example News</a>.</p>\n<!-- /wp:paragraph -->\n\n<!-- wp:paragraph -->\n"
                    "<p>Analysts said the deal signals growing appetite for IP claims.</p>\n"
                    "<!-- /wp:paragraph -->"),
    }
    assert "https://wp.example.test/wp-json/wp/v2/media" in media
    assert f"@{wp.image}" in media
    assert payload(featured) == {"featured_media": 7}
    assert payload(publish) == {"status": "publish"}
    assert "URL: https://wp.example.test/story/" in result.stdout


def test_category_overrides_replace_the_defaults(wp):
    result, calls = wp(WP_CAT_OVERRIDE="238", WP_CAT2_OVERRIDE="86")
    assert result.returncode == 0, result.stderr
    assert payload(calls[0])["categories"] == [238, 86]
    assert len(calls) == 2                       # no image given: create, then publish


def test_failed_create_exits_without_publishing(wp):
    result, calls = wp(str(wp.image), FAKE_CREATE_CODE="400")
    assert result.returncode == 1
    assert "Error: HTTP 400" in result.stdout
    assert len(calls) == 1
