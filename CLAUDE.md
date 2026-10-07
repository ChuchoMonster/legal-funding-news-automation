# CLAUDE.md — Legal Funding Journal (LFJ)

## What This Is
Daily litigation finance news roundup for [Legal Funding Journal](https://legalfundingjournal.com/). We search for articles, write blurbs, and create LinkedIn posts.

## Triggers
- **"Search"** / **"Cover"** / **"Blurb"** / **"Daily run"** — **All the same trigger. One uninterrupted run — never stop to ask which articles to cover.**
  1. Run the full multi-region search (see Daily Workflow below). **48-hour hard limit — except Monday** (see below).
  2. Cross-check every candidate against LFJ via the WP REST search endpoint; drop anything already covered.
  3. **Select the qualifying articles yourself.** Do not present a list and wait for approval — that step is gone.
  4. Write blurbs, generate images, publish live to WordPress.
  5. Wait **30 seconds**, then schedule that day's posts to the LFJ LinkedIn page via Buffer.
  6. Report a short summary only: headlines, LFJ links, Buffer times. No blurb text in chat.

  **Auto-selection rules** (applied without asking):
  - **Cover** genuine litigation-finance industry news: funder news, funding deals, regulatory/legislative developments, court rulings on funding, M&A, personnel moves, industry commentary and reports.
  - **Skip** anything failing the 48-hour window, already on LFJ, pure stock-price pieces, legal aid / nonprofit / charity-funded litigation, or an article you cannot actually read (paywall, 403, unverifiable) — never fabricate facts to fill a blurb.
  - **Cap: 4 articles per run** (5 on Monday), most newsworthy first. If more qualify, cover the top ones and list the rest as skipped.
  - If nothing qualifies, publish nothing and say so plainly. Do NOT widen the window to find something.

  **Monday exception to the 48-hour rule:** nothing runs Friday, Saturday or Sunday, so the Monday run reaches back **96 hours** — to Thursday 8 AM ET, exactly where Thursday's run stopped. That covers Thursday afternoon, Friday and the weekend with no gap. Every other day is the standard rolling 48 hours. The automated run computes the exact cutoff and passes it in, so use the date it gives you rather than working it out yourself.

  The editor reviews after the fact and deletes anything unwanted. For the old present-and-wait behavior, use **"search only"**.
- **"Search only"** — same search and selection, but present the candidate list and wait instead of publishing.

## Automation (launchd — runs on a Mac)
| Job | When (ET) | Script |
|---|---|---|
| `com.lfj.daily-run` | Mon–Thu 8:00 AM | `automation/daily_run.sh` |
| `com.lfj.newsletter` | Thu 10:00 AM | `automation/newsletter_run.sh` |
| `com.lfj.daily-catchup` | every login/boot | `automation/daily_run.sh --catchup` |
| `com.lfj.newsletter-catchup` | every login/boot | `automation/newsletter_run.sh --catchup` |

Logs land in `automation/logs/`. Manage with
`launchctl bootout/bootstrap gui/$UID ~/Library/LaunchAgents/<label>.plist`.

**If the Mac is asleep** at the scheduled time, launchd fires the job on wake.
**If the Mac is powered off**, launchd rebuilds its schedule at boot and the missed run is
dropped entirely — so the two `-catchup` jobs run at every login and rescue it. They are a
no-op unless a run was genuinely missed: right weekday, past the scheduled hour, no
completed run in today's log, and before the cutoff (2 PM daily / 6 PM newsletter). Past
the cutoff they just post a Mac notification instead of publishing late. A lock directory
(`automation/.daily.lock`, `.newsletter.lock`) stops a catch-up from colliding with a run
already in flight; anything older than 90 minutes is treated as stale and reclaimed.

## Output
- Saved here in this directory
- Naming: `YYYY-MM-DD-short-slug-blurb.md` and `YYYY-MM-DD-short-slug-image.jpg`

## Daily Workflow
1. **Search** for litigation finance articles from the last 48 hours ONLY
   - **HARD LIMIT: 48 hours.** Never cover articles published more than 48 hours before the current date and time. If no articles are found within this window, report that — do NOT extend the search window.
   - Terms: litigation finance, litigation funding, legal funding, commercial litigation funding, third-party funding, consumer legal funding, legal expenses insurance, PACCAR
   - **Run multiple parallel searches across four regions:**
     - Broad keyword searches
     - Wire services: PR Newswire, Business Wire, GlobeNewsWire (same-day releases)
     - **U.S. sources:** law.com, law360.com, bloomberglaw.com, Reuters, ABA Journal, JD Supra, National Law Review, Insurance Journal, Gen Re, Property Casualty 360, CLS Blue Sky Blog, Insurance Business Magazine
     - **UK sources:** Legal Futures, Law Gazette, The Lawyer, Legal Cheek, Lexology, CDR (Commercial Dispute Resolution)
     - **Canada sources:** Canadian Lawyer, The Lawyer's Daily, Law Times, Mondaq (Canada)
     - **Australia sources:** Lawyerly, Australian Financial Review, Omni Bridgeway news, Litigation Lending, IMF Bentham
     - State/provincial legislative news: litigation funding bills/regulatory developments
   - **Direct source checks (added 2026-08-17 — these are NOT in Google News, so keyword searching will never find them):**
     - **Policy / think tanks:** [Civitas publications](https://www.civitas.org.uk/publications/), Institute for Legal Reform, Civil Justice Council, Legal Services Board. Reports get written up by paywalled nationals, so go to the publisher — the report itself is usually free.
     - **Australian insolvency:** [Insolvency Insider Australia](https://www.insolvencyinsider-au.com/), [Murrays Legal](https://murrayslegal.com.au/blog/). Funded preference/assigned-claim judgments surface here and nowhere else.
     - **Regulatory disclosures:** EQS/DGAP ad-hoc announcements (German listed companies disclosing funding agreements) and RNS/Investegate (UK). These are mandatory filings, so they are primary sources and always readable.
   - **Non-English searching:** Google News filters by language/region *before* matching, so an English-only query returns zero German or French results no matter what. `search_news_rss.py` now runs DE/FR/ES/NL/GB/AU locales — keep it that way.
      - **Exclude:** pure stock price articles, legal aid/nonprofit/charity-funded litigation
   - **Cross-check against LFJ:** Before presenting results, fetch `https://legalfundingjournal.com/` and compare headlines. If a story is already covered on LFJ, **exclude it — no exceptions** — unless the new article covers the story from a substantially different angle or is a meaningful update to the existing LFJ piece.
2. **Present** articles with: subject line, date, publication, hyperlinked URL, one-sentence summary
3. **User selects** which to summarize
4. **For each selected article**, produce:

### Blurb (200-300 words, aim ~250)
- Headline (subject line)
- Short, concise paragraphs in LFJ's professional, neutral tone
- Second paragraph: "As reported by [Publication Name](article-URL)..."
- Publication name hyperlinked with raw article URL (no UTM/tracking params)
- **Generate featured image** using Kie.ai Nano Banana 2 (via `generate_image.py`)
  - **No text on images** — never include words, letters, or typography in the prompt
  - **No people on images** — never depict human beings, figures, faces, hands, or silhouettes. Keep scenes unpopulated (empty rooms, objects, architecture, abstract forms). This avoids reader perceptions that a depicted person represents litigation finance negatively.
  - **Rotate between these visual styles** (cycle through, don't repeat the same style consecutively):
    1. **Geometric abstract** — bold shapes, clean angles, flat color blocks (think Mondrian meets corporate)
    2. **Photorealistic scene** — empty courtroom, office, or legal setting with cinematic lighting (no people present)
    3. **Isometric illustration** — 3D-style icons/objects arranged in an isometric grid
    4. **Watercolor editorial** — loose, painterly brushstrokes with muted professional tones
    5. **Paper cut / layered collage** — dimensional paper layers with shadows, rich textures
    6. **Dark moody gradient** — deep blues/purples/blacks with subtle light accents, minimal shapes
  - Save as `YYYY-MM-DD-short-slug-image.jpg` in the project directory
- **Auto-publish to WordPress** with featured image (via `post-to-wp.sh <blurb.md> <image.jpg>`)
  - Script creates the post as a draft, attaches the featured image, then publishes it live
  - Categories: Premium, Commercial
  - Tags: Litigation Funding
  - Content converted to Gutenberg blocks
  - Featured image uploaded and attached automatically
  - No author is set (removed from WP backend)
  - "Is featured image a logo" is now pre-checked in WP — no manual step needed

### LinkedIn Post — schedule to Buffer (the 2nd half of "Cover", runs automatically)
- **Not a separate trigger** — this runs automatically as part of **Cover**, after all the day's articles are published and a **30-second wait**.
- **What happens:** auto-schedule that day's LFJ posts to the **LFJ LinkedIn page** via Buffer — no chat blurbs, no Finder.
1. **Find the day's posts:** WP REST (`?per_page=10&_embed`) — take posts dated **today** (America/New_York). For each, grab the title, LFJ permalink, and the **featured image `source_url`** (`_embedded.wp:featuredmedia[0].source_url`) — this public URL is the post image (no local files needed). (You'll typically already have these from the publish step you just ran.)
2. **Write each post** in LFJ LinkedIn style:
   - Same headline as the blurb
   - Two short, punchy paragraphs summarizing it (each sentence on its own line)
   - Five hashtags: `#LitigationFinance #LegalFunding #litfin #LegalIndustry #ThirdPartyFunding`
   - **LFJ article URL** below the hashtags
3. **Schedule via Buffer** to the LFJ LinkedIn page, **live (auto-publish)**, at one-hour increments from now — first post **+1h**, second **+2h**, third **+3h**, etc. Save text to a temp file and run:
   ```bash
   python3 buffer_post.py \
     --text-file <post.txt> --image-url "<WP featured image URL>" --at "YYYY-MM-DD HH:MM"
   ```
   (`buffer_post.py`: channel from `BUFFER_CHANNEL_ID`, ET timezone baked in; `--at` is local ET; attaches the image by URL. Omit `--draft` for live.)
4. **Confirm only:** report which posts were scheduled, for what ET times, with the LFJ links + Buffer post IDs. Don't print the full blurbs.
- Buffer token: `BUFFER_API_TOKEN` in `.env`; LinkedIn page channel: `BUFFER_CHANNEL_ID` in `.env`.

## Thursday Newsletter (weekly)
**Trigger:** "LFJ Newsletter"

### Process
1. Fetch `https://legalfundingjournal.com/` to get the week's article URLs
2. Write newsletter copy as a markdown file (`YYYY-MM-DD-newsletter.md`)
3. Run `python3 send-newsletter.py <newsletter.md>` to create the Mailchimp campaign (creates it as a **draft**; note the campaign ID it prints)
4. **Send a test email to `$NEWSLETTER_TEST_EMAIL`** (see below), then report the campaign ID + that the test went out
5. **Schedule the campaign for the closest Friday at 9:00 AM Eastern** (see below), then read the campaign back to confirm `status: schedule` and the correct `send_time`

Steps 4 and 5 are part of the trigger — no separate instruction needed. Do NOT use `send-newsletter.py --schedule`: it computes "next Friday," which is wrong when the newsletter is written on a Friday. Use the API call below with an explicit timestamp.

#### Step 4 — Test email
```bash
source .env; DC=$(echo "$MAILCHIMP_API_KEY" | sed 's/.*-//')
curl -s -w "\n%{http_code}" -X POST \
  "https://${DC}.api.mailchimp.com/3.0/campaigns/<CAMPAIGN_ID>/actions/test" \
  -u "anystring:${MAILCHIMP_API_KEY}" -H "Content-Type: application/json" \
  -d "{\"test_emails\":[\"${NEWSLETTER_TEST_EMAIL}\"],\"send_type\":\"html\"}"
```
`HTTP 204` with an empty body = success. A test send does not change the campaign's draft status.

#### Step 5 — Schedule for Friday 9 AM ET
Convert 9:00 AM Eastern to UTC for the target Friday — **EDT (Mar–Nov) = 13:00 UTC, EST (Nov–Mar) = 14:00 UTC**. Mailchimp requires 15-minute increments.
```bash
source .env; DC=$(echo "$MAILCHIMP_API_KEY" | sed 's/.*-//')
curl -s -w "\n%{http_code}" -X POST \
  "https://${DC}.api.mailchimp.com/3.0/campaigns/<CAMPAIGN_ID>/actions/schedule" \
  -u "anystring:${MAILCHIMP_API_KEY}" -H "Content-Type: application/json" \
  -d '{"schedule_time":"YYYY-MM-DDT13:00:00+00:00"}'
```
Then verify — never report scheduled without reading it back:
```bash
curl -s -u "anystring:${MAILCHIMP_API_KEY}" \
  "https://${DC}.api.mailchimp.com/3.0/campaigns/<CAMPAIGN_ID>?fields=id,status,send_time,emails_sent,recipients.recipient_count"
```
Expect `status: schedule`, `emails_sent: 0`, and `send_time` matching 9 AM ET.

**Which Friday — always the closest one. Never ask; just schedule it.**
- Invoked **Thursday** → schedule for **tomorrow** (Friday) 9 AM ET
- Invoked **Friday before 9 AM ET** → schedule for **today** 9 AM ET
- Any other day → the **next** Friday 9 AM ET

The only case worth raising with the editor is if it's already **past 9 AM ET on a Friday** — that slot is gone, so say so and confirm before scheduling six days out. Everything else is automatic.

**Note for the editor (report, don't ask):** the campaign goes to the full subscriber list and auto-sends at the scheduled time. Mention that it can still be unscheduled in Mailchimp up until then.

### Newsletter Markdown Format
```
Subject: Headline 1, Headline 2, and Headline 3

Hey folks,

Paragraph 1 with [hyperlinked phrase](https://legalfundingjournal.com/slug/)... (2-4 sentences)

Paragraph 2... (2-4 sentences)

Paragraph 3...

Paragraph 4...

Paragraph 5...

Paragraph 6...

## What to Watch Next Week

- Bullet 1
- Bullet 2
- Bullet 3
```

### Section 1: One Short Paragraph Per Article
- Opens with "Hey folks,"
- **Cover SIX articles — one small paragraph each.** (Changed from four, 2026-08-13.) If fewer than six were published that week, cover everything there is and say so.
- **HARD CAP: 2-4 sentences per paragraph — never more.** No big blocks of text. If a paragraph runs past four sentences, cut it, don't split it into two paragraphs on the same article.
- Keep individual sentences short and punchy. Blank line between every paragraph so the email reads as small, scannable chunks rather than a wall of text.
- Hyperlink one key phrase per paragraph to the **LFJ article URL** (not original source)
- Subject line: comma-separated article highlights (matches existing newsletter style)

### Section 2: What to Watch Next Week (three bullets)
- Three one-sentence forward-looking takeaways from Section 1

## Style
- Professional yet accessible, neutral and objective
- Short, concise paragraphs
- Descriptive, specific headlines
- Source attribution early and explicit
- Formal business vocabulary, minimal colloquialisms
- Always use "%" — never spell out "percent"
