#!/usr/bin/env python3
"""Generate a featured image for an LFJ blurb using Kie.ai Nano Banana 2."""

import json
import os
import sys
import time
from pathlib import Path

try:
    import requests
except ImportError:
    print("Error: 'requests' module not found. Install with: pip3 install requests")
    sys.exit(1)

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)

API_KEY = os.environ.get("KIE_API_KEY", "")
if not API_KEY:
    print("Error: KIE_API_KEY not set in .env")
    sys.exit(1)

CREATE_URL = "https://api.kie.ai/api/v1/jobs/createTask"
STATUS_URL = "https://api.kie.ai/api/v1/jobs/recordInfo"
HEADERS = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json",
}


def create_image_task(prompt: str) -> str:
    """Submit image generation task. Returns task_id."""
    payload = {
        "model": "nano-banana-2",
        "input": {
            "prompt": prompt,
            "image_input": [],
            "google_search": False,
            "aspect_ratio": "16:9",
            "resolution": "1K",
            "output_format": "jpg",
        },
    }
    resp = requests.post(CREATE_URL, headers=HEADERS, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"Kie API error: {data.get('msg', 'Unknown error')}")
    task_id = data.get("data", {}).get("taskId", "")
    if not task_id:
        # Some responses put taskId at top level of data
        task_id = data.get("data", "")
    return task_id


def poll_for_result(task_id: str, max_wait: int = 240, interval: int = 5) -> str:
    """Poll until image is ready. Returns image URL."""
    elapsed = 0
    while elapsed < max_wait:
        resp = requests.get(
            STATUS_URL,
            headers={"Authorization": f"Bearer {API_KEY}"},
            params={"taskId": task_id},
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        state = data.get("state", "")

        if state == "success":
            result_json = json.loads(data.get("resultJson", "{}"))
            urls = result_json.get("resultUrls", [])
            if urls:
                return urls[0]
            raise RuntimeError("Task succeeded but no image URL in response")

        if state == "fail":
            raise RuntimeError(
                f"Image generation failed: {data.get('failMsg', 'Unknown')}"
            )

        print(f"  Status: {state} (waited {elapsed}s)...", file=sys.stderr)
        time.sleep(interval)
        elapsed += interval

    raise TimeoutError(f"Image generation timed out after {max_wait}s")


def download_image(url: str, output_path: str) -> str:
    """Download image to local file."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return output_path


STYLES = {
    "corporate": (
        "Style: Clean, modern, corporate. Muted blue and gray color palette. "
        "Abstract or symbolic representation of the legal/financial concept."
    ),
    "watercolor": (
        "Style: Soft watercolor illustration with warm tones. "
        "Subtle washes of amber, teal, and ivory. Elegant and understated."
    ),
    "geometric": (
        "Style: Bold geometric shapes and patterns. Deep navy, gold, and white. "
        "Modern minimalist design with sharp lines and angular composition."
    ),
    "editorial": (
        "Style: Classic editorial illustration like a newspaper or magazine. "
        "Rich earth tones, sepia and dark green. Engraving-inspired fine detail."
    ),
}


def generate_image(headline: str, output_path: str, style: str = "corporate") -> str:
    """Generate a featured image for an LFJ article and save it locally.

    Args:
        headline: The article headline to base the image on.
        output_path: Where to save the downloaded image.
        style: One of 'corporate', 'watercolor', 'geometric', 'editorial'.

    Returns:
        Path to the saved image file.
    """
    style_text = STYLES.get(style, STYLES["corporate"])
    prompt = (
        f"Professional editorial illustration for a legal news article. "
        f"Topic: {headline}. "
        f"{style_text} "
        f"No text or words in the image. "
        f"Do NOT include any people, human figures, faces, hands, or silhouettes — "
        f"keep the scene completely unpopulated (objects, architecture, or abstract forms only). "
        f"Suitable as a blog featured image."
    )

    print(f"Generating image for: {headline}", file=sys.stderr)
    task_id = create_image_task(prompt)
    print(f"  Task ID: {task_id}", file=sys.stderr)

    image_url = poll_for_result(task_id)
    print(f"  Image ready: {image_url}", file=sys.stderr)

    download_image(image_url, output_path)
    print(f"  Saved to: {output_path}", file=sys.stderr)
    return output_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python3 generate_image.py <headline> <output_path> [style]")
        print("Styles: corporate (default), watercolor, geometric, editorial")
        print('Example: python3 generate_image.py "Republicans Split Over LitFi Regulation" image.jpg watercolor')
        sys.exit(1)

    headline = sys.argv[1]
    output = sys.argv[2]
    style = sys.argv[3] if len(sys.argv) > 3 else "corporate"
    generate_image(headline, output, style)
    print(f"Done: {output}")
