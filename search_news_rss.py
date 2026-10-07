#!/usr/bin/env python3
"""Search Google News RSS for litigation finance articles (past 48 hours).

Free fallback for search_news.py when SerpAPI is unavailable or out of quota.
Uses Google News' native `when:2d` operator for a true 48-hour window.
"""

import json
import sys
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# --- Vocabulary sweep: stories that already call themselves litigation funding -------
QUERIES = [
    '"litigation funding"',
    '"litigation finance"',
    '"legal funding"',
    '"third-party funding"',
    '"third party litigation funding"',
    '"commercial litigation funding"',
    '"consumer legal funding"',
    '"litigation funder"',
    '"legal expenses insurance"',
    '"after the event insurance"',
    'PACCAR litigation funding',
    '"class action" funder',
    '"pre-settlement funding"',
    '"legal finance"',

    # --- Entity sweep (added 2026-08-17) ---------------------------------------
    # ROOT CAUSE of the 777 Partners / LCM Recoveries misses: the funder watchlist was
    # only ever used to FILTER results the vocabulary queries had already returned. A
    # story about a known funder whose headline avoids funding words (bankruptcy,
    # judgment, M&A) was structurally invisible. These query the names directly.
    '"Burford Capital" OR "Omni Bridgeway" OR "Litigation Capital Management" OR "LCM Recoveries"',
    '"Harbour Litigation" OR "Therium" OR "Woodsford" OR "Longford Capital" OR "Parabellum Capital"',
    '"777 Partners" OR "Fortress Investment" OR "Certum Group" OR "Innsworth" OR "Nera Capital"',
    '"Manolete Partners" OR "Legal-Bay" OR "Rocade Capital" OR "Balance Legal Capital" OR "Augusta Ventures"',

    # --- Deal / claim-monetisation vocabulary (added 2026-08-17) ----------------
    # Catches funding deals framed as asset purchases rather than "funding" — the
    # Angel Deal Syndicate / Charge Zone shape (a claim bought out of a bankruptcy
    # estate at auction, then enforced).
    '"claims monetization" OR "claim monetisation" OR "litigation investment"',
    '"assigned claim" OR "claim assignment" liquidator',
    '"funding agreement" lawsuit OR damages',

    # --- Court / judgment vocabulary (added 2026-08-17) ------------------------
    # Catches rulings ABOUT funders that never say "litigation funding" up top.
    '"unfair preference" OR "preference claim" funder OR assignee',
    'funder "adverse costs" OR "security for costs"',
    ('"litigation funder" OR "litigation funding" court OR judgment OR tribunal', "AU"),
    ('liquidator "funding agreement" OR "assigned claim"', "AU"),

    # --- Policy / think-tank output (added 2026-08-17) -------------------------
    ('Civitas OR "Institute for Legal Reform" OR "Civil Justice Council" litigation funding', "GB"),
    ('"litigation funding" report OR review OR consultation', "GB"),

    # --- Non-English (added 2026-08-17) ---------------------------------------
    # ROOT CAUSE of the elumeo miss: an EQS ad-hoc disclosure in German. Every query was
    # English AND the feed was pinned to hl=en-US, so German results were filtered out
    # before matching. Verified 2026-08-17: the same query returns 0 under US/English and
    # finds the elumeo ad-hoc under DE/German.
    ('Prozessfinanzierer OR Prozessfinanzierung', "DE"),
    ('"financement de litige" OR "financement de procès"', "FR"),
    ('"financiación de litigios" OR "financiación de pleitos"', "ES"),
    ('procesfinanciering OR "derdenfinanciering"', "NL"),
]

# Google News language/region tuples: hl, gl, ceid
LOCALES = {
    "US": ("en-US", "US", "US:en"),
    "GB": ("en-GB", "GB", "GB:en"),
    "AU": ("en-AU", "AU", "AU:en"),
    "DE": ("de", "DE", "DE:de"),
    "FR": ("fr", "FR", "FR:fr"),
    "ES": ("es", "ES", "ES:es"),
    "NL": ("nl", "NL", "NL:nl"),
}

EXCLUDED_DOMAINS = ["legalfundingjournal.com"]

# Window is overridable so the Monday 96-hour catch-up run actually reaches back that far.
# Previously `when:2d` was hardcoded, which silently capped every Monday run at 48 hours.
#   search_news_rss.py            -> 48h (Tue-Thu)
#   search_news_rss.py 96         -> 96h (Monday)
WINDOW_HOURS = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 48
WINDOW_DAYS = max(1, -(-WINDOW_HOURS // 24))   # ceil to whole days for Google's when:Nd
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120 Safari/537.36"


def fetch(query, locale: str = "US") -> list[dict]:
    """Fetch one Google News RSS query.

    `locale` matters more than it looks: the feed was previously pinned to
    hl=en-US&gl=US&ceid=US:en, which made every non-English query return ZERO
    results — Google filters to the requested language/region before matching.
    That is why the German elumeo/Prozessfinanzierer story was unreachable.
    """
    hl, gl, ceid = LOCALES.get(locale, LOCALES["US"])
    url = (
        "https://news.google.com/rss/search?q="
        + urllib.parse.quote(f"{query} when:{WINDOW_DAYS}d")
        + f"&hl={hl}&gl={gl}&ceid={ceid}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=45) as r:
            root = ET.fromstring(r.read())
    except Exception as e:
        print(f"  Warning: failed for {query}: {e}", file=sys.stderr)
        return []

    out = []
    for item in root.findall(".//item"):
        title = item.findtext("title") or ""
        link = item.findtext("link") or ""
        pub = item.findtext("pubDate") or ""
        source_el = item.find("source")
        source = source_el.text if source_el is not None else ""
        # Google appends " - Source" to titles; strip it
        if source and title.endswith(f" - {source}"):
            title = title[: -(len(source) + 3)]
        try:
            dt = parsedate_to_datetime(pub)
        except Exception:
            dt = None
        out.append({"title": title, "link": link, "source": source, "pubDate": pub, "dt": dt})
    return out


def main() -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    seen: dict[str, dict] = {}
    failures = []

    for entry in QUERIES:
        # An entry is either "query" (US/English) or ("query", "LOCALE").
        q, loc = entry if isinstance(entry, tuple) else (entry, "US")
        print(f"Searching [{loc}]: {q}...", file=sys.stderr)
        results = fetch(q, loc)
        if not results:
            failures.append(f"[{loc}] {q}")
        for a in results:
            link = a["link"]
            if not link or link in seen:
                continue
            if any(d in link for d in EXCLUDED_DOMAINS):
                continue
            if a["dt"] and a["dt"] < cutoff:
                continue
            seen[link] = a

    # Fail loudly rather than reporting a false "quiet news day"
    if len(failures) == len(QUERIES):
        print("\nERROR: every query failed — this is a tool failure, NOT an empty result.",
              file=sys.stderr)
        return 2

    articles = sorted(seen.values(), key=lambda a: a["dt"] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)

    if failures:
        print(f"\nWarning: {len(failures)} of {len(QUERIES)} queries failed: {failures}",
              file=sys.stderr)

    print(f"\n=== {len(articles)} unique articles (past {WINDOW_HOURS} hours) ===\n")
    for i, a in enumerate(articles, 1):
        stamp = a["dt"].strftime("%Y-%m-%d %H:%M UTC") if a["dt"] else a["pubDate"]
        print(f"{i}. {a['title']}")
        print(f"   Source: {a['source']} | {stamp}")
        print(f"   {a['link']}")
        print()

    out_path = Path(__file__).parent / "latest_rss_results.json"
    with open(out_path, "w") as f:
        json.dump([{k: v for k, v in a.items() if k != "dt"} for a in articles], f, indent=2)
    print(f"Saved to: {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
