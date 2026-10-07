#!/bin/bash
# LFJ weekly newsletter — launchd, Thursday 10:00am ET.
#
# Writes the week's newsletter, creates the Mailchimp campaign as a draft, sends a test
# to $NEWSLETTER_TEST_EMAIL, and schedules it for TOMORROW (Friday) 9:00am ET.
# The Friday timestamp is computed here (DST-correct) rather than left to the model.
#
# Manual test:  automation/newsletter_run.sh
# Catch-up (run at login; no-op unless Thursday's run is genuinely missing):
#               automation/newsletter_run.sh --catchup

set -uo pipefail

# Paths: override via environment; defaults assume this script lives in <repo>/automation.
CLAUDE="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
LFJ_DIR="${LFJ_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOGDIR="$LFJ_DIR/automation/logs"
PY="/usr/bin/python3"
STAMP="$(date +%F)"
LOG="$LOGDIR/newsletter-$STAMP.log"
LOCK="$LFJ_DIR/automation/.newsletter.lock"
CATCHUP_CUTOFF_HOUR=18   # still leaves plenty of room before the Friday 9am send

# Test-send recipient: read from the environment, falling back to the repo's .env.
if [ -z "${NEWSLETTER_TEST_EMAIL:-}" ] && [ -f "$LFJ_DIR/.env" ]; then
  NEWSLETTER_TEST_EMAIL="$(grep -E '^NEWSLETTER_TEST_EMAIL=' "$LFJ_DIR/.env" | head -1 | cut -d= -f2- | tr -d '"'"'"'')"
fi
: "${NEWSLETTER_TEST_EMAIL:?set NEWSLETTER_TEST_EMAIL in the environment or .env}"

mkdir -p "$LOGDIR"
cd "$LFJ_DIR" || exit 1

notify() { osascript -e "display notification \"$1\" with title \"LFJ Newsletter\"" >/dev/null 2>&1 || true; }

# --- Catch-up guard -----------------------------------------------------------
# A run missed while the Mac was POWERED OFF is dropped by launchd at boot. Missing
# a Thursday costs the whole week's send, so recover it at login. No-op otherwise.
if [ "${1:-}" = "--catchup" ]; then
  DOW="$(date +%u)"    # 1=Mon .. 7=Sun
  HOUR="$(date +%H)"; HOUR="${HOUR#0}"
  [ "$DOW" -ne 4 ] && exit 0                                   # only Thursday is scheduled
  [ "$HOUR" -lt 10 ] && exit 0                                 # today's 10am run hasn't happened yet
  grep -q "^===== exit=" "$LOG" 2>/dev/null && exit 0          # already completed today
  if [ "$HOUR" -ge "$CATCHUP_CUTOFF_HOUR" ]; then
    notify "Thursday's newsletter run was missed. Run automation/newsletter_run.sh by hand before Friday 9am."
    exit 0
  fi
  notify "Recovering this morning's missed newsletter run..."
  set -- ""
fi

# --- Single-run lock ----------------------------------------------------------
if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then
  echo "another newsletter run is already in progress — exiting"; exit 0
fi
rm -rf "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { echo "could not take lock — exiting"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Next Friday 9:00am America/New_York, expressed in UTC. Run on a Thursday this is
# tomorrow; run any other day it rolls forward to the coming Friday.
read -r FRIDAY_LOCAL SCHEDULE_UTC <<<"$("$PY" - <<'PYEOF'
from datetime import datetime, timedelta, time
from zoneinfo import ZoneInfo
et = ZoneInfo("America/New_York")
now = datetime.now(et)
days = (4 - now.weekday()) % 7          # Monday=0 ... Friday=4
if days == 0 and now.time() >= time(9): # past 9am Friday already, go to next week
    days = 7
target = datetime.combine((now + timedelta(days=days)).date(), time(9), tzinfo=et)
print(target.strftime("%Y-%m-%d"),
      target.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%S+00:00"))
PYEOF
)"

PROMPT="Run the LFJ Thursday Newsletter for the week ending $STAMP. This is an automated,
unattended run — there is no human to answer questions. Make every call yourself and do
not stop for confirmation at any point.

Follow the 'Thursday Newsletter' section of CLAUDE.md exactly:
1. Fetch https://legalfundingjournal.com/ and gather this week's article URLs.
2. Write the newsletter to ${STAMP}-newsletter.md — six articles, one paragraph each,
   HARD CAP 2-4 sentences per paragraph, one hyperlinked phrase per paragraph pointing at
   the LFJ article URL, plus the 'What to Watch Next Week' section with three bullets.
   If fewer than six articles were published this week, cover everything there is and say so.
3. Run: /usr/bin/python3 send-newsletter.py ${STAMP}-newsletter.md
   Note the campaign ID it prints. Do NOT pass --schedule.
4. Send the test email to $NEWSLETTER_TEST_EMAIL using the curl call in CLAUDE.md.
   HTTP 204 with an empty body means success.
5. Schedule the campaign with the schedule action, using EXACTLY this timestamp — it has
   already been computed for you, DST included, so do not recalculate it:
       \"schedule_time\":\"$SCHEDULE_UTC\"
   That is Friday $FRIDAY_LOCAL at 9:00 AM Eastern.
6. Read the campaign back and confirm status is 'schedule', emails_sent is 0, and
   send_time matches. Never report it scheduled without reading it back.

Finish with a short report: campaign ID, the six headlines, confirmation the test went
out, and the verified send time. Remind the editor it goes to the full subscriber list, auto-sends at
that time, and can still be unscheduled in Mailchimp until then.

Use /usr/bin/python3 for all python scripts."

{
  echo "===== LFJ newsletter — $(date '+%F %T %Z') ====="
  echo "Target send: Friday $FRIDAY_LOCAL 9:00am ET  ($SCHEDULE_UTC)"
} >> "$LOG"
OUT="$(mktemp -t lfj_newsletter)"
trap 'rm -f "$OUT"; rmdir "$LOCK" 2>/dev/null' EXIT

"$CLAUDE" -p "$PROMPT" \
  --permission-mode bypassPermissions \
  --model claude-opus-5 \
  --fallback-model claude-sonnet-5 > "$OUT" 2>&1
RC=$?
cat "$OUT" >> "$LOG"
echo "===== exit=$RC finished $(date '+%F %T %Z') =====" >> "$LOG"

# A model/API failure can still exit 0, so treat an API Error line as a failure too.
# Grep only THIS run's output, not the whole day's appended log.
if [ "$RC" -ne 0 ] || grep -q "^API Error:" "$OUT"; then
  MSG="FAILED — check automation/logs/newsletter-$STAMP.log"
else
  MSG="Newsletter scheduled for Fri $FRIDAY_LOCAL 9am ET"
fi
notify "$MSG"
echo "$MSG"
