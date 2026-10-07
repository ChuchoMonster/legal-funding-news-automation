#!/usr/bin/env python3
"""
Post / schedule a LinkedIn post to the LFJ company page via Buffer's GraphQL API.

Usage:
  # schedule for a specific local time (America/New_York)
  python3 buffer_post.py --text-file post.txt --at "2026-06-23 09:00"

  # add to the next open slot in Buffer's queue
  python3 buffer_post.py --text-file post.txt --queue

  # publish immediately
  python3 buffer_post.py --text-file post.txt --now

  # stage as a Buffer draft (does NOT publish; review in Buffer UI)
  python3 buffer_post.py --text-file post.txt --at "2026-06-23 09:00" --draft

Text can come from --text-file or --text. Reads BUFFER_API_TOKEN and
BUFFER_CHANNEL_ID from the environment or .env.
Nothing is published unless mode is --now/--at/--queue without --draft.
"""
import argparse, json, os, sys, urllib.request
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

ENV_PATH   = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
API_URL    = "https://api.buffer.com"
TZ         = ZoneInfo("America/New_York")

def load_env_value(key):
    """Read a value from the environment, falling back to the local .env file."""
    val = os.environ.get(key)
    if not val and os.path.exists(ENV_PATH):
        for line in open(ENV_PATH):
            line = line.strip()
            if line.startswith(f"{key}="):
                val = line.split("=", 1)[1].strip().strip('"').strip("'")
    return val

# Buffer channel for the LinkedIn company page (find it via Buffer's GraphQL `channels` query).
CHANNEL_ID = load_env_value("BUFFER_CHANNEL_ID")

def load_token():
    tok = load_env_value("BUFFER_API_TOKEN")
    if not tok:
        sys.exit("BUFFER_API_TOKEN not found (env or .env).")
    return tok

def gql(token, query, variables):
    body = json.dumps({"query": query, "variables": variables}).encode()
    req = urllib.request.Request(API_URL, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read().decode())

CREATE = """
mutation($in: CreatePostInput!) {
  createPost(input: $in) {
    __typename
    ... on PostActionSuccess { post { id status dueAt text channelId } }
    ... on RestProxyError { message code link }
    ... on InvalidInputError { message }
    ... on LimitReachedError { message }
    ... on UnauthorizedError { message }
    ... on NotFoundError { message }
    ... on UnexpectedError { message }
  }
}"""

def main():
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text"); src.add_argument("--text-file")
    when = ap.add_mutually_exclusive_group(required=True)
    when.add_argument("--at", help='local time "YYYY-MM-DD HH:MM" (America/New_York)')
    when.add_argument("--queue", action="store_true", help="next open queue slot")
    when.add_argument("--now", action="store_true", help="publish immediately")
    ap.add_argument("--draft", action="store_true", help="stage as Buffer draft (no publish)")
    ap.add_argument("--image-url", help="public image URL to attach (e.g. WP featured image)")
    ap.add_argument("--channel-id", default=CHANNEL_ID)
    a = ap.parse_args()
    if not a.channel_id:
        sys.exit("BUFFER_CHANNEL_ID not set (env or .env), and no --channel-id given.")

    text = a.text if a.text else open(a.text_file, encoding="utf-8").read().strip()
    if not text:
        sys.exit("Empty post text.")

    inp = {
        "channelId": a.channel_id,
        "text": text,
        "schedulingType": "automatic",   # Buffer auto-publishes (vs 'notification' reminder)
        "assets": [{"image": {"url": a.image_url}}] if a.image_url else [],
        "saveToDraft": bool(a.draft),
    }
    if a.now:
        inp["mode"] = "shareNow"
    elif a.queue:
        inp["mode"] = "addToQueue"
    else:
        dt_local = datetime.strptime(a.at, "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
        inp["mode"] = "customScheduled"
        inp["dueAt"] = dt_local.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")

    res = gql(load_token(), CREATE, {"in": inp})
    if res.get("errors"):
        print("GraphQL error:", json.dumps(res["errors"], indent=2)); sys.exit(1)
    payload = res["data"]["createPost"]
    if payload["__typename"] != "PostActionSuccess":
        print(f"FAILED ({payload['__typename']}): {payload.get('message')}"); sys.exit(1)
    p = payload["post"]
    print("OK — post created.")
    print(f"  id:       {p['id']}")
    print(f"  status:   {p['status']}{'  (DRAFT — not published)' if a.draft else ''}")
    print(f"  dueAt:    {p.get('dueAt')}")
    print(f"  channel:  {p['channelId']} (LFJ LinkedIn page)")

if __name__ == "__main__":
    main()
