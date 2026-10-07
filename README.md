# Legal Funding News Automation

![tests](https://github.com/ChuchoMonster/legal-funding-news-automation/actions/workflows/tests.yml/badge.svg)

Automation behind [Legal Funding Journal](https://legalfundingjournal.com/), a daily
litigation-finance news publication. Four mornings a week an unattended agent run
searches global news, filters out anything already covered, writes short news blurbs,
generates a featured image, publishes to WordPress and schedules LinkedIn posts. A weekly
run assembles and schedules the email newsletter.

> **Code only.** This is a sanitized public copy. Credentials, subscriber data, logs,
> search results, generated images and the archive of published articles are not
> included. The files in `examples/` are published public news write-ups kept to
> show the output format. All IDs, emails and keys come from environment variables.

## How it works

```
launchd (Mon-Thu 8am ET)
  -> automation/daily_run.sh        computes the search window (48h, or 96h on Mondays),
                                     takes a lock, injects any carryover stories,
                                     then runs Claude Code headless with CLAUDE.md as the playbook
       -> search_news_rss.py         Google News RSS, ~30 queries across 7 locales/languages
       -> search_news.py             SerpAPI Google News (with quota check + retries)
       -> WordPress REST search      drop stories already covered
       -> generate_image.py          Kie.ai Nano Banana 2 featured image (no text, no people)
       -> post-to-wp.sh              markdown -> Gutenberg blocks -> draft -> image -> publish
       -> buffer_post.py             schedule LinkedIn posts at +1h, +2h, ... via Buffer GraphQL

launchd (Thu 10am ET)
  -> automation/newsletter_run.sh   computes Friday 9am ET in UTC (DST-correct)
       -> send-newsletter.py         clone last Mailchimp campaign's HTML, swap in new content
       -> Mailchimp test send + schedule + read-back verification
```

The model makes the editorial calls (what qualifies, what to write); the scripts do every
side effect. Deterministic logic such as date windows and send times is computed in the
shell wrapper and passed in, so the model never does date arithmetic.

Reliability details worth noting:
- **Catch-up mode** (`--catchup`): launchd drops jobs missed while the machine was off, so
  a login hook re-runs the day's job only if it is genuinely missing and still before a cutoff.
- **Stale-lock recovery**: a lock directory older than 90 minutes is reclaimed.
- **Fail loud, not empty**: search scripts distinguish "every query failed" from "quiet news day".
- **Never live without an image**: posts are created as drafts, the image attached, then published.

## Scripts

| File | What it does |
|---|---|
| `automation/daily_run.sh` | Scheduled daily run: window calculation, lock, carryover queue, headless agent invocation, notifications |
| `automation/newsletter_run.sh` | Scheduled weekly newsletter run with DST-correct scheduling and read-back check |
| `automation/carryover.md` | Queue of stories to force into the next run (consumed on success) |
| `CLAUDE.md` | Agent playbook: selection rules, sources, blurb/LinkedIn/newsletter formats, style |
| `search_news_rss.py` | Free Google News RSS search; vocabulary, entity, deal, court and non-English sweeps |
| `search_news.py` | SerpAPI Google News search with quota check, retries and client-side 48h filter |
| `search-articles.sh` | Thin wrapper that loads `.env` and runs `search_news.py` |
| `generate_image.py` | Featured-image generation via Kie.ai (async task + polling), four style presets |
| `post-to-wp.sh` | Publish a markdown blurb to WordPress with categories, tag and featured image |
| `buffer_post.py` | Create/schedule/draft a LinkedIn post through Buffer's GraphQL API |
| `send-newsletter.py` | Build a Mailchimp campaign from a markdown newsletter, optional scheduling |
| `contributed_article_publish.py` | Pull a guest contributor's emailed Word doc (via `gws` Gmail CLI), convert to WordPress blocks, publish, draft a reply |

## Setup

Requirements: macOS (launchd, `osascript` notifications), Python 3.9+, `curl`,
[Claude Code](https://claude.com/claude-code) for the scheduled runs, and the `gws`
Google Workspace CLI for `contributed_article_publish.py` only.

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in values
python3 search_news_rss.py            # 48h search, prints results
python3 search_news_rss.py 96         # 96h window
./post-to-wp.sh examples/2026-03-02-kpmg-us-legal-services-blurb.md image.jpg
python3 buffer_post.py --text-file post.txt --at "2026-06-23 09:00" --draft
python3 send-newsletter.py examples/sample-newsletter.md     # creates a draft campaign
automation/daily_run.sh --dry-run     # search + selection only, publishes nothing
```

To schedule, create launchd agents that call `automation/daily_run.sh` (Mon-Thu 08:00),
`automation/newsletter_run.sh` (Thu 10:00) and both with `--catchup` at login.

## Tests

- `pip install -r requirements-dev.txt`, then `pytest` from the repo root.
- Covers the 48-hour freshness filter, dedupe and own-site exclusion, SerpAPI retries and quota checks, and Google News RSS parsing across locales.
- Covers what gets sent out: the WordPress post payload (`post-to-wp.sh` is run for real against a fake `curl`), contributed-article Word parsing, Buffer requests, image prompts, and newsletter HTML assembly against Mailchimp.
- No network and no credentials: every HTTP call, `curl` and Gmail call is replaced with a fake, and all test data is fictional.
- Runs on every push and pull request via GitHub Actions.

## Environment variables

| Variable | Used by |
|---|---|
| `WP_SITE_URL`, `WP_USERNAME`, `WP_APP_PASSWORD` | WordPress REST API (application password) |
| `WP_CAT_PREMIUM`, `WP_CAT_COMMERCIAL`, `WP_CAT_CONSUMER`, `WP_CAT_PUBLIC`, `WP_TAG_LITIGATION_FUNDING` | WordPress category/tag IDs |
| `SERPAPI_KEY` | `search_news.py` |
| `KIE_API_KEY` | `generate_image.py` |
| `MAILCHIMP_API_KEY`, `MAILCHIMP_LIST_ID`, `MAILCHIMP_SEGMENT_ID` (optional) | `send-newsletter.py` |
| `NEWSLETTER_FROM_NAME`, `NEWSLETTER_REPLY_TO`, `NEWSLETTER_TEST_EMAIL` | Newsletter sender settings and test recipient |
| `BUFFER_API_TOKEN`, `BUFFER_CHANNEL_ID` | `buffer_post.py` |
| `CONTRIBUTOR_EMAIL`, `CONTRIBUTOR_BYLINE` | `contributed_article_publish.py` |
| `CLAUDE_BIN`, `LFJ_DIR` (optional) | Override paths in the scheduled-run scripts |

## Not included

Subscriber lists, financial records, outreach tooling and contact data, run logs,
search-result JSON, generated images, the published-article archive and agent skill files.
