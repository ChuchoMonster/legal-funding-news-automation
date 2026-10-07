#!/bin/bash
# LFJ daily run — launchd, Mon–Thu 8:00am ET.
#
# One uninterrupted pass: search -> pick -> blurb -> image -> publish live to WP
# -> wait 30s -> schedule the day's posts to LinkedIn via Buffer.
# No human approval step. John reviews after the fact and says what to delete.
#
# Manual test:  automation/daily_run.sh
# Dry run (search + pick only, publishes nothing):  automation/daily_run.sh --dry-run
# Catch-up (run at login; no-op unless today's run is genuinely missing):
#                                                   automation/daily_run.sh --catchup

set -uo pipefail

# Paths: override via environment; defaults assume this script lives in <repo>/automation.
CLAUDE="${CLAUDE_BIN:-$(command -v claude || echo "$HOME/.local/bin/claude")}"
LFJ_DIR="${LFJ_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
LOGDIR="$LFJ_DIR/automation/logs"
STAMP="$(date +%F)"
LOG="$LOGDIR/daily-$STAMP.log"
LOCK="$LFJ_DIR/automation/.daily.lock"
CATCHUP_CUTOFF_HOUR=14   # past this, don't auto-publish — just tell John

mkdir -p "$LOGDIR"
cd "$LFJ_DIR" || exit 1

notify() { osascript -e "display notification \"$1\" with title \"LFJ Daily Run\"" >/dev/null 2>&1 || true; }

# --- Catch-up guard -----------------------------------------------------------
# launchd rebuilds its schedule at boot, so a run missed while the Mac was POWERED
# OFF is dropped entirely (unlike sleep, which fires on wake). This mode runs at
# login and rescues that case. It is a no-op in every other situation.
if [ "${1:-}" = "--catchup" ]; then
  DOW="$(date +%u)"    # 1=Mon .. 7=Sun
  HOUR="$(date +%H)"; HOUR="${HOUR#0}"
  [ "$DOW" -gt 4 ] && exit 0                                   # no run scheduled Fri/Sat/Sun
  [ "$HOUR" -lt 8 ] && exit 0                                  # today's 8am run hasn't happened yet
  grep -q "^===== exit=" "$LOG" 2>/dev/null && exit 0          # today's run already completed
  if [ "$HOUR" -ge "$CATCHUP_CUTOFF_HOUR" ]; then
    notify "Today's 8am run was missed and it's now too late to auto-publish. Run automation/daily_run.sh by hand if you still want it."
    exit 0
  fi
  notify "Recovering this morning's missed run..."
  set -- ""   # fall through into a normal live run
fi

# --- Single-run lock ----------------------------------------------------------
# A power-off leaves the lock behind, so treat anything older than 90 min as stale.
if [ -d "$LOCK" ] && [ -z "$(find "$LOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then
  echo "another daily run is already in progress — exiting"; exit 0
fi
rm -rf "$LOCK" 2>/dev/null
mkdir "$LOCK" 2>/dev/null || { echo "could not take lock — exiting"; exit 0; }
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Search window, computed here so the model never does date math.
#   Mon      -> back 96h, i.e. Thursday 08:00 ET — exactly where Thursday's run stopped.
#               Covers Thursday afternoon, Friday and the weekend with no gap, since
#               nothing runs Fri/Sat/Sun.
#   Tue-Thu  -> the standard rolling 48 hours.
read -r WIN_START WIN_DESC WIN_CAP <<<"$(/usr/bin/python3 - <<'PYEOF'
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
now = datetime.now(ZoneInfo("America/New_York"))
if now.weekday() == 0:  # Monday
    print((now - timedelta(hours=96)).strftime("%Y-%m-%d_%H:%M"), "MONDAY_CATCHUP", 5)
else:
    print((now - timedelta(hours=48)).strftime("%Y-%m-%d_%H:%M"), "ROLLING_48H", 4)
PYEOF
)"
WIN_START="${WIN_START/_/ }"

if [ "$WIN_DESC" = "MONDAY_CATCHUP" ]; then
  WINDOW_RULE="This is the MONDAY run, the exception to the usual 48-hour limit. Nothing
   runs Friday, Saturday or Sunday, so today's window reaches back 96 hours to pick up
   Thursday afternoon, Friday and the weekend. Cover anything published on or after
   **$WIN_START ET**. Do not go back further than that."
else
  WINDOW_RULE="HARD 48-hour limit: cover only articles published on or after
   **$WIN_START ET**. Never widen the window beyond that."
fi

# --- Carryover queue ----------------------------------------------------------
# Stories John explicitly asked to cover on the next run, exempt from the window rule.
# Covered first, still duplicate-checked, and counted toward the day's cap.
CARRY_FILE="$LFJ_DIR/automation/carryover.md"
CARRY_RULE=""
CARRY_N=0
if [ -f "$CARRY_FILE" ] && grep -q '^- \*\*' "$CARRY_FILE"; then
  CARRY_N="$(grep -c '^- \*\*' "$CARRY_FILE")"
  CARRY_RULE="

CARRYOVER QUEUE — $CARRY_N story/stories John explicitly asked to cover on this run.
These are EXEMPT from the window rule above: cover them even though they are older.
Cover them FIRST, before anything from the search, and count them toward today's cap of
$WIN_CAP. Still duplicate-check each one against LFJ and skip it if it is already covered.
Honour any NOTE about attribution. If a queued source is unreadable, skip it and say so
rather than inventing detail.

$(cat "$CARRY_FILE")
"
fi

DRY=""
if [ "${1:-}" = "--dry-run" ]; then
  DRY="
DRY RUN: do every search, duplicate-check and selection step, then STOP. Report which
articles you WOULD have covered and why. Do not generate images, do not publish to
WordPress, do not touch Buffer."
fi

PROMPT="Run the LFJ daily run for $STAMP. This is an automated, unattended run — there is
no human to answer questions, so never pause to ask which articles to cover. Follow the
\"Search / Cover / Daily run\" trigger in CLAUDE.md end to end and make every judgment call
yourself.

Work through it in this order:
1. Search for litigation finance articles across all four regions and the wire services,
   per the Daily Workflow in CLAUDE.md. $WINDOW_RULE
2. Cross-check every candidate against Legal Funding Journal using the WP REST search
   endpoint. Drop anything already covered.
3. Select the qualifying articles yourself using the auto-selection rules in CLAUDE.md.
   Cap at $WIN_CAP, most newsworthy first.
4. For each: write the blurb, generate the featured image (rotate styles, no text, no
   people), and publish LIVE to WordPress with post-to-wp.sh.
5. After ALL articles are published, wait 30 seconds, then schedule that day's posts to
   the LFJ LinkedIn page via buffer_post.py — live, at one-hour increments from now
   (+1h, +2h, +3h, ...), each with its WP featured-image URL.
6. Finish with a SHORT report: each headline, its LFJ link, its Buffer time and post ID,
   plus anything you deliberately skipped and why. No blurb text.

If no article passes the window and duplicate filters, publish nothing and say exactly
that. An empty day is a correct outcome, not a failure to work around. Never fabricate
facts for an article you could not actually read — skip it instead.

Use /usr/bin/python3 for all python scripts.$CARRY_RULE$DRY"

OUT="$(mktemp -t lfj_daily)"
trap 'rm -f "$OUT"; rmdir "$LOCK" 2>/dev/null' EXIT

echo "===== LFJ daily run — $(date '+%F %T %Z') =====" >> "$LOG"
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
  MSG="FAILED — check automation/logs/daily-$STAMP.log"
else
  MSG="Daily run finished — see automation/logs/daily-$STAMP.log"
  # Consume the queue only on a successful, non-dry run, so a failure retries it tomorrow.
  if [ "$CARRY_N" -gt 0 ] && [ "${1:-}" != "--dry-run" ]; then
    cp "$CARRY_FILE" "$LOGDIR/carryover-$STAMP.done.md"
    /usr/bin/sed -i '' '/^- \*\*/,$d' "$CARRY_FILE"
    printf '\n' >> "$CARRY_FILE"
    MSG="$MSG (cleared $CARRY_N carryover item(s))"
  fi
fi
notify "$MSG"
echo "$MSG"
