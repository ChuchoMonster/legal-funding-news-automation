#!/usr/bin/env python3
"""Send the LFJ weekly newsletter via Mailchimp.

Replicates the most recent campaign, replaces article content and
'What to Watch' bullets, then schedules for Friday 9 AM Eastern.

Usage:
    python3 send-newsletter.py <newsletter.md>
    python3 send-newsletter.py <newsletter.md> --schedule
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
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

API_KEY = os.environ.get("MAILCHIMP_API_KEY", "")
if not API_KEY:
    print("Error: MAILCHIMP_API_KEY not set in .env")
    sys.exit(1)

# Extract data center from API key (e.g., "xxx-us3" -> "us3")
DC = API_KEY.split("-")[-1]
BASE_URL = f"https://{DC}.api.mailchimp.com/3.0"
AUTH = ("apikey", API_KEY)

# Audience settings (from Mailchimp: Audience > Settings > Audience name and defaults).
LIST_ID = os.environ.get("MAILCHIMP_LIST_ID", "")
SEGMENT_ID = os.environ.get("MAILCHIMP_SEGMENT_ID", "")  # optional saved segment
FROM_NAME = os.environ.get("NEWSLETTER_FROM_NAME", "Legal Funding Journal")
REPLY_TO = os.environ.get("NEWSLETTER_REPLY_TO", "")
if not LIST_ID or not REPLY_TO:
    print("Error: MAILCHIMP_LIST_ID and NEWSLETTER_REPLY_TO must be set in .env")
    sys.exit(1)

# Inline styles matching the existing template
P_STYLE = (
    "margin: 10px 0;padding: 0;"
    "mso-line-height-rule: exactly;"
    "-ms-text-size-adjust: 100%;-webkit-text-size-adjust: 100%;"
    "color: #202020;font-family: Helvetica;font-size: 16px;"
    "line-height: 150%;text-align: left;"
)
LINK_STYLE = (
    "mso-line-height-rule: exactly;"
    "-ms-text-size-adjust: 100%;-webkit-text-size-adjust: 100%;"
    "color: #ea5b3a;font-weight: normal;text-decoration: underline;"
)
LI_STYLE = (
    "mso-line-height-rule: exactly;"
    "-ms-text-size-adjust: 100%;-webkit-text-size-adjust: 100%;"
)


def parse_newsletter(md_path: str) -> dict:
    """Parse newsletter markdown into subject, paragraphs, and bullets."""
    text = Path(md_path).read_text().strip()
    lines = text.split("\n")

    # Extract subject line
    subject = ""
    content_start = 0
    for i, line in enumerate(lines):
        if line.startswith("Subject:"):
            subject = line[len("Subject:"):].strip()
            content_start = i + 1
            break

    rest = "\n".join(lines[content_start:]).strip()

    # Split at "## What to Watch Next Week"
    parts = re.split(r"##\s*What to Watch Next Week", rest, maxsplit=1)
    article_text = parts[0].strip()
    bullets_text = parts[1].strip() if len(parts) > 1 else ""

    # Parse article paragraphs (split on blank lines)
    paragraphs = [p.strip() for p in article_text.split("\n\n") if p.strip()]

    # Parse bullets
    bullets = []
    for line in bullets_text.split("\n"):
        line = line.strip()
        if line.startswith("- "):
            bullets.append(line[2:].strip())

    return {
        "subject": subject,
        "paragraphs": paragraphs,
        "bullets": bullets,
    }


def md_links_to_html(text: str) -> str:
    """Convert markdown [text](url) links to styled HTML <a> tags."""
    def replace_link(m):
        link_text = m.group(1)
        url = m.group(2)
        return (
            f'<a href="{url}" rel="noopener noreferrer" '
            f'target="_blank" style="{LINK_STYLE}">{link_text}</a>'
        )
    return re.sub(r"\[([^\]]+)\]\(([^)]+)\)", replace_link, text)


def build_paragraphs_html(paragraphs: list) -> str:
    """Convert paragraphs to styled HTML <p> tags."""
    html_parts = []
    for para in paragraphs:
        html_para = md_links_to_html(para)
        html_parts.append(f'<p style="{P_STYLE}">{html_para}</p>\n')
    return "\n".join(html_parts)


def build_bullets_html(bullets: list) -> str:
    """Convert bullets to styled HTML <ul>/<li> tags."""
    items = []
    for i, bullet in enumerate(bullets):
        br = "<br>\n\t&nbsp;" if i < len(bullets) - 1 else ""
        items.append(f'\t<li style="{LI_STYLE}">{bullet}{br}</li>')
    return "<ul>\n" + "\n".join(items) + "\n</ul>"


def api_get(endpoint: str) -> dict:
    resp = requests.get(f"{BASE_URL}{endpoint}", auth=AUTH, timeout=30)
    resp.raise_for_status()
    return resp.json()


def api_post(endpoint: str, data: dict = None) -> dict:
    resp = requests.post(
        f"{BASE_URL}{endpoint}", auth=AUTH, json=data or {}, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def api_patch(endpoint: str, data: dict) -> dict:
    resp = requests.patch(
        f"{BASE_URL}{endpoint}", auth=AUTH, json=data, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def api_put(endpoint: str, data: dict) -> dict:
    resp = requests.put(
        f"{BASE_URL}{endpoint}", auth=AUTH, json=data, timeout=30
    )
    resp.raise_for_status()
    return resp.json()


def find_latest_sent_campaign() -> str:
    """Find the most recently sent campaign ID."""
    data = api_get(
        "/campaigns?sort_field=send_time&sort_dir=DESC&count=5"
        "&fields=campaigns.id,campaigns.status,campaigns.settings.subject_line"
    )
    for c in data.get("campaigns", []):
        if c.get("status") == "sent":
            print(f"  Latest campaign: {c['id']}")
            print(f"  Subject: {c['settings']['subject_line']}")
            return c["id"]
    raise RuntimeError("No sent campaigns found")


def create_campaign_from_template(source_campaign_id: str, subject: str, preview_text: str) -> tuple:
    """Create a new campaign and grab the HTML from the source campaign.

    Drag-and-drop campaigns can't have their HTML overridden via PUT after
    replication (the template binding takes precedence). Instead, we:
    1. GET the rendered HTML from the source campaign
    2. Create a brand-new campaign (no template binding)
    3. Return (new_campaign_id, source_html)
    """
    # Get the rendered HTML from the source
    content = api_get(f"/campaigns/{source_campaign_id}/content")
    source_html = content.get("html", "")
    if not source_html:
        raise RuntimeError("No HTML content in source campaign")

    today = datetime.now().strftime("%Y-%m-%d")

    # Create a new campaign without template binding
    campaign_data = {
        "type": "regular",
        "recipients": {"list_id": LIST_ID},
        "settings": {
            "subject_line": subject,
            "title": f"LFJ Newsletter - {today}",
            "from_name": FROM_NAME,
            "reply_to": REPLY_TO,
            "auto_footer": False,
            "preview_text": preview_text,
        },
        "tracking": {
            "opens": True,
            "html_clicks": True,
        },
    }

    if SEGMENT_ID:
        campaign_data["recipients"]["segment_opts"] = {"saved_segment_id": int(SEGMENT_ID)}

    data = api_post("/campaigns", campaign_data)
    new_id = data.get("id", "")
    if not new_id:
        raise RuntimeError(f"Campaign creation failed: {data}")

    return new_id, source_html


def replace_article_content(html: str, new_paragraphs_html: str) -> str:
    """Replace the article paragraphs in the newsletter HTML."""
    # Find the "Hey folks," paragraph and replace all <p> tags in that
    # content block up to the dark background "What to Watch" header.
    # The article section is a series of <p> tags inside a mcnTextContent <td>.
    # We find "Hey folks," and replace from that <p> through the closing </td>.

    # Find the <p> containing "Hey folks,"
    hey_match = re.search(r'<p[^>]*>Hey folks,</p>', html)
    if not hey_match:
        raise RuntimeError("Could not find 'Hey folks,' paragraph in template HTML")

    start = hey_match.start()

    # Find the closing </td> after the article paragraphs.
    # The article content td ends before the "What to Watch" heading block.
    # Look for the </td> that comes after the last <p> and before the
    # dark background table (background-color: #404040).
    dark_bg = html.find("background-color: #404040", start)
    if dark_bg < 0:
        raise RuntimeError("Could not find 'What to Watch' header block in template HTML")

    # Find the </td> just before the dark background section
    # Walk backwards from dark_bg to find the closing </td>
    td_close = html.rfind("</td>", start, dark_bg)
    if td_close < 0:
        raise RuntimeError("Could not find closing </td> for article content block")

    # Also need to go back further to find the end of the last </p> before </td>
    # The content between start and td_close is what we replace
    old_content = html[start:td_close].rstrip()

    new_html = html[:start] + new_paragraphs_html + "\n" + html[td_close:]
    return new_html


def replace_bullets_content(html: str, new_bullets_html: str) -> str:
    """Replace the What to Watch bullet list in the newsletter HTML."""
    # Find the <ul> after "What to Watch Next Week"
    watch_idx = html.find("What to Watch Next Week")
    if watch_idx < 0:
        raise RuntimeError("Could not find 'What to Watch Next Week' in template HTML")

    # Find the <ul> after this heading
    ul_start = html.find("<ul", watch_idx)
    if ul_start < 0:
        raise RuntimeError("Could not find <ul> after 'What to Watch Next Week'")

    ul_end = html.find("</ul>", ul_start)
    if ul_end < 0:
        raise RuntimeError("Could not find closing </ul>")
    ul_end += len("</ul>")

    new_html = html[:ul_start] + new_bullets_html + html[ul_end:]
    return new_html


def next_friday_9am_et() -> str:
    """Calculate next Friday at 9 AM Eastern in UTC ISO format."""
    # Eastern time is UTC-5 (EST) or UTC-4 (EDT)
    # March-November is EDT (UTC-4), November-March is EST (UTC-5)
    now = datetime.now(timezone.utc)
    # Find next Friday
    days_ahead = 4 - now.weekday()  # Friday = 4
    if days_ahead <= 0:
        days_ahead += 7
    next_fri = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(
        days=days_ahead
    )

    # Determine if EDT or EST based on month (rough heuristic)
    month = next_fri.month
    if 3 <= month <= 10:
        # EDT: 9 AM ET = 13:00 UTC
        utc_hour = 13
    else:
        # EST: 9 AM ET = 14:00 UTC
        utc_hour = 14

    schedule_time = next_fri.replace(hour=utc_hour, minute=0)
    return schedule_time.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 send-newsletter.py <newsletter.md> [--schedule]")
        sys.exit(1)

    md_path = sys.argv[1]
    do_schedule = "--schedule" in sys.argv

    if not Path(md_path).exists():
        print(f"Error: File not found: {md_path}")
        sys.exit(1)

    # Step 1: Parse newsletter markdown
    print("Parsing newsletter...")
    newsletter = parse_newsletter(md_path)
    print(f"  Subject: {newsletter['subject']}")
    print(f"  Paragraphs: {len(newsletter['paragraphs'])}")
    print(f"  Bullets: {len(newsletter['bullets'])}")

    if not newsletter["subject"]:
        print("Error: No 'Subject:' line found in markdown")
        sys.exit(1)

    # Step 2: Find latest campaign and create new one with its HTML
    print("Finding latest sent campaign...")
    latest_id = find_latest_sent_campaign()
    preview = newsletter["paragraphs"][0][:90] if newsletter["paragraphs"] else ""
    print("Creating new campaign from template HTML...")
    new_id, html = create_campaign_from_template(
        latest_id, newsletter["subject"], preview
    )
    print(f"  New campaign ID: {new_id}")

    # Step 3: Modify HTML content
    print("Updating newsletter content...")

    # Build new HTML content
    paragraphs_html = build_paragraphs_html(newsletter["paragraphs"])
    bullets_html = build_bullets_html(newsletter["bullets"])

    # Replace article content
    html = replace_article_content(html, paragraphs_html)
    print("  Article paragraphs replaced")

    # Replace bullets
    html = replace_bullets_content(html, bullets_html)
    print("  What to Watch bullets replaced")

    # Step 5: PUT updated content
    api_put(f"/campaigns/{new_id}/content", {"html": html})
    print("  Content saved")

    # Step 6: Run send checklist
    print("Running send checklist...")
    checklist = api_get(f"/campaigns/{new_id}/send-checklist")
    all_ready = True
    for item in checklist.get("items", []):
        status = "OK" if item.get("is_ready") else "WARN"
        if not item.get("is_ready"):
            all_ready = False
        print(f"  [{status}] {item.get('heading', 'Unknown')}: {item.get('details', '')}")

    if not all_ready:
        print("\nWarning: Some checklist items are not ready. Review in Mailchimp.")

    # Step 7: Schedule if requested
    if do_schedule:
        schedule_time = next_friday_9am_et()
        print(f"\nScheduling for {schedule_time} (Friday 9 AM ET)...")
        api_post(f"/campaigns/{new_id}/actions/schedule", {
            "schedule_time": schedule_time,
        })
        print("  Newsletter scheduled!")
    else:
        print(f"\nCampaign created as draft (not scheduled).")
        print(f"  Review in Mailchimp and schedule manually, or re-run with --schedule")

    print(f"\n  Campaign URL: https://{DC}.admin.mailchimp.com/campaigns/edit?id={new_id}")
    print("Done!")


if __name__ == "__main__":
    main()
