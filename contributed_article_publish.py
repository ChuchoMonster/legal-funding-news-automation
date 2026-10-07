#!/usr/bin/env python3
"""contributed_article_publish.py — publish a guest contributor's article to WordPress.

Finds the contributor's email by subject in Gmail (via the `gws` CLI), pulls the
attached Word document and image, and publishes the article verbatim to WordPress:
  - Byline paragraph first (CONTRIBUTOR_BYLINE, e.g. "The following was contributed by ...")
  - Word Title  -> post title;  Heading 1/2 -> bold paragraph
  - Categories WP_CAT_CONSUMER + WP_CAT_PUBLIC, tag WP_TAG_LITIGATION_FUNDING
  - Attached image -> featured image
Creates a draft, attaches the image, then publishes (never live without the image).
Last, drafts (never sends) a reply to the contributor in the same thread with the live link.

Usage:
  python3 contributed_article_publish.py "<email subject>" [--dry-run]
  python3 contributed_article_publish.py "<subject>" --reply-only <live URL>   # reply draft only
  python3 contributed_article_publish.py "<subject>" --article-url <URL>       # publish from a web page
Requires the `gws` CLI to be authenticated against the inbox that receives submissions.
"""
import warnings; warnings.filterwarnings("ignore")
import argparse, base64, html, json, os, re, subprocess, sys, tempfile, zipfile
import requests

# The site firewall 403s python-requests' default User-Agent; curl's is allowed.
HTTP = requests.Session()
HTTP.headers["User-Agent"] = "curl/8.7.1"

HERE = os.path.dirname(os.path.abspath(__file__))


def load_env():
    env = dict(os.environ)
    path = os.path.join(HERE, ".env")
    if os.path.exists(path):
        for line in open(path):
            m = re.match(r"\s*(?:export\s+)?([A-Z0-9_]+)=(.*)", line)
            if m:
                env.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
    return env


ENV = load_env()
SENDER = ENV.get("CONTRIBUTOR_EMAIL", "")   # address the contributor submits from
BYLINE = ENV.get("CONTRIBUTOR_BYLINE", "The following was contributed by a guest author.")
CAT_CONSUMER = int(ENV.get("WP_CAT_CONSUMER", "0"))
CAT_PUBLIC = int(ENV.get("WP_CAT_PUBLIC", "0"))
TAG_LIT_FUNDING = int(ENV.get("WP_TAG_LITIGATION_FUNDING", "0"))
IMG_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}


def gws(*args):
    out = subprocess.run(["gws", *args], capture_output=True, text=True).stdout
    return json.loads(out[out.index("{"):])


def find_email(subject, need_docx=True):
    if not SENDER:
        sys.exit("CONTRIBUTOR_EMAIL is not set (env or .env).")
    q = f'from:{SENDER} subject:"{subject}" has:attachment'
    res = gws("gmail", "users", "messages", "list", "--params",
              json.dumps({"userId": "me", "q": q, "maxResults": 10}))
    ids = [m["id"] for m in res.get("messages", [])]
    if not ids:
        sys.exit(f"No email from {SENDER} with subject '{subject}' and an attachment.")
    msgs = [gws("gmail", "users", "messages", "get", "--params",
                json.dumps({"userId": "me", "id": i, "format": "full"})) for i in ids]
    # Replies in the thread carry only the signature logo; take the newest one with a .docx.
    msgs.sort(key=lambda m: int(m["internalDate"]), reverse=True)
    if not need_docx:
        return msgs[0]
    for m in msgs:
        if any(p.get("filename", "").lower().endswith(".docx") for p in walk(m["payload"])):
            return m
    sys.exit("Found the email but no Word document attached.")


def walk(p):
    yield p
    for q in p.get("parts", []) or []:
        yield from walk(q)


def download(msg, part, dest_dir):
    data = gws("gmail", "users", "messages", "attachments", "get", "--params",
               json.dumps({"userId": "me", "messageId": msg["id"], "id": part["body"]["attachmentId"]}))["data"]
    path = os.path.join(dest_dir, part["filename"])
    open(path, "wb").write(base64.urlsafe_b64decode(data))
    return path


def pick_attachments(msg, dest_dir):
    docx = image = None
    for p in walk(msg["payload"]):
        fn = p.get("filename") or ""
        ext = os.path.splitext(fn.lower())[1]
        if ext == ".docx" and not docx:
            docx = download(msg, p, dest_dir)
        # Skip inline signature logos (image001.png etc.)
        elif ext in IMG_TYPES and not re.match(r"image\d+\.", fn.lower()) and not image:
            image = download(msg, p, dest_dir)
    return docx, image


def run_html(run):
    text = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", run))
    text = html.escape(html.unescape(text), quote=False)
    if "<w:br/>" in run or "<w:br " in run:
        text += "<br>"
    if not text:
        return ""
    rpr = (re.search(r"<w:rPr>(.*?)</w:rPr>", run, re.S) or [None, ""])[1]
    if re.search(r'<w:b/>|<w:b w:val="(1|true)"', rpr):
        text = f"<strong>{text}</strong>"
    if re.search(r'<w:i/>|<w:i w:val="(1|true)"', rpr):
        text = f"<em>{text}</em>"
    return text


def parse_docx(path):
    z = zipfile.ZipFile(path)
    doc = z.read("word/document.xml").decode("utf8")
    rels = {}
    if "word/_rels/document.xml.rels" in z.namelist():
        for rid, tgt in re.findall(r'Id="([^"]+)"[^>]*Target="([^"]+)"',
                                   z.read("word/_rels/document.xml.rels").decode("utf8")):
            rels[rid] = tgt
    title, paras = None, []
    for p in re.findall(r"<w:p[ >].*?</w:p>", doc, re.S):
        style = (re.search(r'<w:pStyle w:val="([^"]+)"', p) or [None, ""])[1]
        plain = "".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)).strip()
        if not plain:
            continue
        plain = html.unescape(plain)
        if style.lower() == "title" and not title:
            title = plain
            continue
        if title is None:
            continue  # kicker/label above the title (e.g. "CONSUMER LEGAL FUNDING")
        if style.lower().startswith("heading"):
            paras.append(f"<strong>{html.escape(plain, quote=False)}</strong>")
            continue
        body = ""
        for seg in re.findall(r"<w:hyperlink.*?</w:hyperlink>|<w:r[ >].*?</w:r>", p, re.S):
            if seg.startswith("<w:hyperlink"):
                inner = "".join(run_html(r) for r in re.findall(r"<w:r[ >].*?</w:r>", seg, re.S))
                rid = (re.search(r'r:id="([^"]+)"', seg) or [None, None])[1]
                body += f'<a href="{rels[rid]}">{inner}</a>' if rid in rels else inner
            else:
                body += run_html(seg)
        body = body.replace("</strong><strong>", "").replace("</em><em>", "")
        paras.append(body.strip())
    if not title:
        sys.exit("No Title-styled paragraph in the Word document; cannot tell the headline apart.")
    return title, paras


def parse_nlr(url):
    """Some submissions arrive as a National Law Review link instead of a Word doc: publish that text verbatim."""
    t = subprocess.run(["curl", "-sL", "-m", "30", "-A", "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124 Safari/537.36", url], capture_output=True, text=True).stdout
    m = re.search(r'<meta property="og:title" content="([^"]+)"', t)
    if not m:
        sys.exit("Could not read the article title from the National Law Review page.")
    title = html.unescape(m.group(1)).strip()
    i = t.find("field--name-body")
    if i < 0:
        sys.exit("Could not find the article body on the National Law Review page.")
    seg = t[i:]
    paras = []
    for tag, inner in re.findall(r"<(p|h2|h3|h4|blockquote)[^>]*>(.*?)</\1>", seg, re.S):
        plain = html.unescape(re.sub(r"<[^>]+>", "", inner)).replace("\xa0", " ").strip()
        if not plain:
            continue
        if tag in ("h2", "h3", "h4") and plain.startswith(("Current Public Notices", "Current Legal Analysis", "More from", "Upcoming Events")):
            break  # end of the article; the rest is page furniture
        if tag in ("h2", "h3", "h4") or re.match(r"\s*<(strong|b)>.*</(strong|b)>\s*$", inner, re.S):
            paras.append(f"<strong>{html.escape(plain, quote=False)}</strong>")
            continue
        body = re.sub(r"<(?!/?(a|em|i|strong|b)\b)[^>]+>", "", inner)  # keep links + emphasis only
        body = re.sub(r"<(/?)b>", r"<\1strong>", re.sub(r"<(/?)i>", r"<\1em>", body))
        body = re.sub(r'<a [^>]*?href="([^"]+)"[^>]*>', r'<a href="\1">', body).replace("&nbsp;", " ")
        paras.append(body.strip())
    if len(paras) < 5:
        sys.exit(f"Only {len(paras)} paragraphs found on the National Law Review page — not trusting it.")
    paras.append(f'<em>A version of this commentary first appeared in <a href="{url}">The National Law Review</a>.</em>')
    return title, paras


REPLY_BODY = """Hi,

It's live: {url}

Thanks again!
"""


def draft_reply(msg, url):
    """Save (don't send) a reply to the contributor in the same Gmail thread with the live link."""
    from email.mime.text import MIMEText
    thread = gws("gmail", "users", "threads", "get", "--params",
                 json.dumps({"userId": "me", "id": msg["threadId"], "format": "minimal"}))
    last = gws("gmail", "users", "messages", "get", "--params",
               json.dumps({"userId": "me", "id": thread["messages"][-1]["id"], "format": "full"}))
    h = {x["name"].lower(): x["value"] for x in last["payload"]["headers"]}
    subject = h.get("subject", "")
    reply = MIMEText(REPLY_BODY.format(url=url))
    reply["To"] = SENDER
    reply["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if h.get("message-id"):
        reply["In-Reply-To"] = h["message-id"]
        reply["References"] = (h.get("references", "") + " " + h["message-id"]).strip()
    raw = base64.urlsafe_b64encode(reply.as_bytes()).decode()
    d = gws("gmail", "users", "drafts", "create", "--params", json.dumps({"userId": "me"}),
            "--json", json.dumps({"message": {"raw": raw, "threadId": msg["threadId"]}}))
    print(f"Reply to contributor drafted (not sent). Draft ID {d['id']}")


def blocks(paras):
    return "\n\n".join(f"<!-- wp:paragraph -->\n<p>{p}</p>\n<!-- /wp:paragraph -->" for p in paras)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("subject")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reply-only", metavar="URL",
                    help="skip publishing; just draft the reply to the contributor with this live URL")
    ap.add_argument("--article-url", metavar="URL",
                    help="the submission is a National Law Review link instead of a Word doc: publish that article")
    a = ap.parse_args()

    msg = find_email(a.subject, need_docx=not a.article_url)
    if a.reply_only:
        draft_reply(msg, a.reply_only)
        return
    tmp = tempfile.mkdtemp(prefix="arc_")
    docx, image = pick_attachments(msg, tmp)
    title, paras = parse_nlr(a.article_url) if a.article_url else parse_docx(docx)
    content = blocks([html.escape(BYLINE, quote=False)] + paras)
    print(f"Title: {title}\nParagraphs: {len(paras)}\nImage: {image or 'NONE'}")

    site, auth = ENV["WP_SITE_URL"], (ENV["WP_USERNAME"], ENV["WP_APP_PASSWORD"])
    dupes = HTTP.get(f"{site}/wp-json/wp/v2/posts", auth=auth, params={
        "search": title, "status": "publish,draft,future", "_fields": "id,title,link,status"}).json()
    dupes = [d for d in dupes if html.unescape(d["title"]["rendered"]).strip().lower() == title.strip().lower()]
    if dupes:
        sys.exit(f"Already on LFJ: {dupes[0]['link']} ({dupes[0]['status']}) — not publishing again.")
    if not image:
        sys.exit("No article image attached — not publishing without a featured image.")
    if a.dry_run:
        print(content)
        return

    r = HTTP.post(f"{site}/wp-json/wp/v2/posts", auth=auth, json={
        "title": title, "content": content, "status": "draft",
        "categories": [CAT_CONSUMER, CAT_PUBLIC], "tags": [TAG_LIT_FUNDING]})
    r.raise_for_status()
    post_id = r.json()["id"]
    ext = os.path.splitext(image.lower())[1]
    with open(image, "rb") as f:
        m = HTTP.post(f"{site}/wp-json/wp/v2/media", auth=auth, data=f.read(), headers={
            "Content-Type": IMG_TYPES[ext],
            "Content-Disposition": f'attachment; filename="{os.path.basename(image).replace(" ", "-")}"'})
    m.raise_for_status()
    HTTP.post(f"{site}/wp-json/wp/v2/posts/{post_id}", auth=auth,
                  json={"featured_media": m.json()["id"]}).raise_for_status()
    p = HTTP.post(f"{site}/wp-json/wp/v2/posts/{post_id}", auth=auth, json={"status": "publish"})
    p.raise_for_status()
    print(f"Published! Post ID {post_id}\nURL: {p.json()['link']}")
    draft_reply(msg, p.json()["link"])


if __name__ == "__main__":
    main()
