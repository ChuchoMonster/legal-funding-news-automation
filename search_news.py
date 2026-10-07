#!/usr/bin/env python3
"""Search Google News via SerpAPI for litigation finance articles (past 24 hours).

Hardened: checks SerpAPI quota before running, surfaces the REAL error on failure
(rate limit / out-of-searches / timeout) instead of silently returning nothing,
and retries once on a transient error.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

# ROOT CAUSE of past "searches failing" (diagnosed 2026-07-31):
#   SerpAPI's Google engine HANGS (http 000, never returns) whenever a `tbs=qdr:*`
#   time filter is sent — regardless of client (bare curl hangs too). Without tbs it
#   returns in ~0.08s. So we DO NOT use tbs; we fetch recent news and filter to the
#   past 48h client-side (see is_recent). This also matches LFJ's 48h hard limit.
# We fetch via `curl` (macOS-native TLS) rather than `requests` for reliability and
# consistency with the rest of the project's scripts.

# Load .env
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key.strip(), value)

API_KEY = os.environ.get("SERPAPI_KEY", "")
if not API_KEY:
    print("Error: SERPAPI_KEY not set")
    sys.exit(1)

SERPAPI_URL = "https://serpapi.com/search.json"
ACCOUNT_URL = "https://serpapi.com/account.json"
LOW_QUOTA_WARN = 25  # warn if fewer than this many searches remain


class FetchError(Exception):
    """Raised when curl fails or returns a transient HTTP error."""


def fetch_json(url: str, params: dict, timeout: int) -> dict:
    """GET url?params via curl and parse JSON. Raises FetchError on transient failure.

    Returns the parsed dict on HTTP 200 (even if it contains a SerpAPI "error"
    field — callers inspect that). Raises FetchError for timeouts / 429 / 5xx so
    the caller can retry.
    """
    full = f"{url}?{urlencode(params)}"
    # -w writes the HTTP code on its own last line; body precedes it.
    try:
        proc = subprocess.run(
            ["curl", "-s", "--max-time", str(timeout), "-w", "\n%{http_code}", full],
            capture_output=True, text=True, timeout=timeout + 10,
        )
    except subprocess.TimeoutExpired:
        raise FetchError(f"timeout after {timeout}s")
    if proc.returncode != 0:
        # curl exit 28 = operation timeout; others = connection/DNS/TLS errors
        kind = "timeout" if proc.returncode == 28 else f"curl exit {proc.returncode}"
        raise FetchError(f"{kind}: {proc.stderr.strip()[:120] or 'no response'}")
    out = proc.stdout.rsplit("\n", 1)
    body, code = (out[0], out[1]) if len(out) == 2 else (proc.stdout, "000")
    if code == "429":
        raise FetchError("HTTP 429 rate limit")
    if code.startswith("5"):
        raise FetchError(f"HTTP {code} server error")
    try:
        return json.loads(body)
    except ValueError:
        raise FetchError(f"HTTP {code}, non-JSON response")

QUERIES = [
    "litigation finance",
    "litigation funding",
    "legal funding",
    "third-party funding",
    "commercial litigation funding",
    "consumer legal funding",
    "legal expenses insurance litigation",
    "PACCAR litigation funding",
]

EXCLUDED_DOMAINS = ["legalfundingjournal.com"]


def check_quota() -> int | None:
    """Print SerpAPI plan status and return searches remaining (None if unknown)."""
    try:
        d = fetch_json(ACCOUNT_URL, {"api_key": API_KEY}, timeout=20)
    except FetchError as e:
        print(f"  Warning: could not read SerpAPI account status: {e}", file=sys.stderr)
        return None
    if "error" in d:
        # A bad/expired key shows up here first — fail loudly, don't run 8 dead queries.
        print(f"ERROR: SerpAPI key rejected by account check: {d['error']}", file=sys.stderr)
        print("       Check SERPAPI_KEY in .env (account: dashboard > Your Private API Key).", file=sys.stderr)
        sys.exit(2)
    left = d.get("total_searches_left")
    plan = d.get("plan_name", "?")
    used = d.get("this_month_usage", "?")
    per_month = d.get("searches_per_month", "?")
    print(f"SerpAPI: {plan} | {used}/{per_month} used this cycle | {left} searches left",
          file=sys.stderr)
    if isinstance(left, int) and left < LOW_QUOTA_WARN:
        print(f"  WARNING: only {left} searches left — a full run uses ~{len(QUERIES)}.",
              file=sys.stderr)
    if isinstance(left, int) and left < len(QUERIES):
        print(f"  WARNING: fewer searches left ({left}) than this run needs "
              f"({len(QUERIES)}); some queries will fail with 'out of searches'.",
              file=sys.stderr)
    return left if isinstance(left, int) else None


REQUEST_TIMEOUT = 60   # SerpAPI's news engine can be slow; give it room
MAX_ATTEMPTS = 3       # 1 initial try + 2 retries on transient failures


def search_google_news(query: str) -> tuple[list[dict], str | None]:
    """Search Google News. Returns (results, error). error is None on success.

    Retries up to 2 times on a transient failure (timeout, connection error,
    429, or 5xx), with a short backoff between attempts.
    """
    params = {
        "engine": "google",
        "q": query,
        "tbm": "nws",
        # NO tbs time filter — it makes SerpAPI hang (see top-of-file note).
        # Google News returns recent-first; we filter to 48h in is_recent().
        "num": 20,
        "hl": "en",   # English UI → English relative dates ("6 days ago") for is_recent
        "gl": "us",
        "api_key": API_KEY,
    }
    last_err = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            data = fetch_json(SERPAPI_URL, params, timeout=REQUEST_TIMEOUT)
        except FetchError as e:
            last_err = str(e)   # transient (timeout / 429 / 5xx) — retry below
        else:
            if data.get("error"):
                # e.g. "Your account has run out of searches." — NOT transient.
                return [], f"SerpAPI error: {data['error']}"
            return data.get("news_results", []), None
        if attempt < MAX_ATTEMPTS:
            backoff = 2 * attempt   # 2s, then 4s
            print(f"  retrying '{query}' ({last_err}; attempt {attempt + 1}/{MAX_ATTEMPTS})...",
                  file=sys.stderr)
            time.sleep(backoff)
    return [], last_err


def is_recent(date_str: str) -> bool:
    """True if the article's 'date' is within the past ~48h (LFJ's hard limit).

    SerpAPI news dates are relative ("5 hours ago", "1 day ago", "2 days ago") or
    absolute ("07/29/2026, 08:00 AM, +0000 UTC"). Keep anything <= 2 days old, and
    keep unknown/absent dates (rather than silently dropping possibly-fresh items).
    """
    d = (date_str or "").strip().lower()
    if not d:
        return True
    if "hour" in d or "minute" in d or "just now" in d or "today" in d:
        return True
    if "yesterday" in d:
        return True
    import re
    m = re.search(r"(\d+)\s+day", d)
    if m:
        return int(m.group(1)) <= 2
    # Absolute date like MM/DD/YYYY — parse and compare against 48h ago.
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", d)
    if m:
        from datetime import datetime, timezone, timedelta
        try:
            dt = datetime(int(m.group(3)), int(m.group(1)), int(m.group(2)), tzinfo=timezone.utc)
            return dt >= datetime.now(timezone.utc) - timedelta(days=2)
        except ValueError:
            return True
    return True  # unknown format → keep, let the human judge


def is_excluded(url: str) -> bool:
    return any(domain in url for domain in EXCLUDED_DOMAINS)


def deduplicate(articles: list[dict]) -> list[dict]:
    seen = set()
    unique = []
    for a in articles:
        url = a.get("link", "")
        if url in seen or is_excluded(url):
            continue
        if not is_recent(a.get("date", "")):
            continue
        seen.add(url)
        unique.append(a)
    return unique


def main():
    check_quota()
    all_results = []
    errors = []  # (query, error_message)
    for query in QUERIES:
        print(f"Searching: {query}...", file=sys.stderr)
        results, err = search_google_news(query)
        if err:
            print(f"  FAILED: {query} -> {err}", file=sys.stderr)
            errors.append((query, err))
        all_results.extend(results)

    # If every query failed, that's a real outage/quota problem — make it loud.
    if errors and len(errors) == len(QUERIES):
        print("\nERROR: all searches failed. Reasons:", file=sys.stderr)
        for q, e in errors:
            print(f"  - {q}: {e}", file=sys.stderr)
        print("\nNo results — this is a SerpAPI failure, not an empty news day.")
        sys.exit(1)

    articles = deduplicate(all_results)

    if not articles:
        note = ""
        if errors:
            note = f" ({len(errors)} of {len(QUERIES)} queries failed — see stderr)"
        print(f"\nNo articles found in the past 24 hours.{note}")
        return

    if errors:
        print(f"\nNote: {len(errors)} of {len(QUERIES)} queries failed "
              f"(partial results below): {[q for q, _ in errors]}", file=sys.stderr)

    print(f"\n=== Found {len(articles)} unique articles (past 24 hours) ===\n")
    for i, a in enumerate(articles, 1):
        title = a.get("title", "No title")
        source = a.get("source", "Unknown")
        date = a.get("date", "Unknown date")
        link = a.get("link", "")
        snippet = a.get("snippet", "")
        print(f"{i}. {title}")
        print(f"   Source: {source} | Date: {date}")
        print(f"   URL: {link}")
        if snippet:
            print(f"   Summary: {snippet}")
        print()

    json_path = Path(__file__).parent / "latest_search_results.json"
    with open(json_path, "w") as f:
        json.dump(articles, f, indent=2)
    print(f"Full results saved to: {json_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
