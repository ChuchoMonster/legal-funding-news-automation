"""contributed_article_publish.py: Word -> WordPress blocks, and the publish sequence."""
import sys
import types
import zipfile

import pytest

from conftest import FakeHTTPResponse

W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def para(text, style=None, bold=False):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    rpr = "<w:rPr><w:b/></w:rPr>" if bold else ""
    return f"<w:p>{ppr}<w:r>{rpr}<w:t>{text}</w:t></w:r></w:p>"


def make_docx(path, body, rels=""):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("word/document.xml", f"<w:document {W}><w:body>{body}</w:body></w:document>")
        if rels:
            z.writestr("word/_rels/document.xml.rels", f"<Relationships>{rels}</Relationships>")
    return str(path)


@pytest.fixture
def cap(load):
    return load("contributed_article_publish.py")


def test_run_html_formatting_and_escaping(cap):
    assert cap.run_html('<w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>Tom &amp; Jerry &lt;LLC&gt;</w:t></w:r>') == \
        "<em><strong>Tom &amp; Jerry &lt;LLC&gt;</strong></em>"
    assert cap.run_html('<w:r><w:rPr><w:b w:val="0"/></w:rPr><w:t>plain</w:t><w:br/></w:r>') == "plain<br>"
    assert cap.run_html("<w:r><w:rPr></w:rPr></w:r>") == ""


def test_parse_docx_structure(cap, tmp_path):
    hyperlink = ('<w:p><w:r><w:t xml:space="preserve">Read </w:t></w:r>'
                 '<w:hyperlink r:id="rId5"><w:r><w:t>the ruling</w:t></w:r></w:hyperlink>'
                 '<w:r><w:rPr><w:b/></w:rPr><w:t>Bold </w:t></w:r>'
                 '<w:r><w:rPr><w:b/></w:rPr><w:t>run</w:t></w:r></w:p>')
    body = (para("CONSUMER LEGAL FUNDING")            # kicker above the title: dropped
            + para("Why Funding Matters", "Title")
            + para("")                                # empty paragraph: skipped
            + para("Background", "Heading1")
            + hyperlink)
    rels = '<Relationship Id="rId5" Type="hyperlink" Target="https://example.com/ruling"/>'
    title, paras = cap.parse_docx(make_docx(tmp_path / "a.docx", body, rels))
    assert title == "Why Funding Matters"
    assert paras == ["<strong>Background</strong>",
                     'Read <a href="https://example.com/ruling">the ruling</a><strong>Bold run</strong>']


def test_parse_docx_without_title_is_refused(cap, tmp_path):
    with pytest.raises(SystemExit, match="No Title-styled paragraph"):
        cap.parse_docx(make_docx(tmp_path / "b.docx", para("Just text")))


def test_blocks_wrap_each_paragraph(cap):
    assert cap.blocks(["One", "<strong>Two</strong>"]) == (
        "<!-- wp:paragraph -->\n<p>One</p>\n<!-- /wp:paragraph -->\n\n"
        "<!-- wp:paragraph -->\n<p><strong>Two</strong></p>\n<!-- /wp:paragraph -->")


def test_pick_attachments_skips_signature_logos(cap, monkeypatch):
    msg = {"payload": {"parts": [
        {"filename": "image001.png"},
        {"filename": "", "parts": [{"filename": "Article.DOCX"}]},
        {"filename": "hero.jpg"},
        {"filename": "second.png"},
    ]}}
    monkeypatch.setattr(cap, "download", lambda m, part, d: f"{d}/{part['filename']}")
    assert cap.pick_attachments(msg, "/tmp/x") == ("/tmp/x/Article.DOCX", "/tmp/x/hero.jpg")


def test_parse_nlr_reads_article_and_stops_at_page_furniture(cap, monkeypatch):
    page = ('<meta property="og:title" content="Funding &amp; Ethics">'
            '<div class="field--name-body">'
            + "".join(f"<p>Paragraph {i} with <b>bold</b> and <span>span</span>.</p>" for i in range(5))
            + '<p><a class="x" href="https://example.com/r" target="_blank">link</a></p>'
            + "<h3>More from the author</h3><p>Not part of the article.</p></div>")
    monkeypatch.setattr(cap.subprocess, "run",
                        lambda cmd, **kw: types.SimpleNamespace(stdout=page))
    title, paras = cap.parse_nlr("https://natlawreview.example/article")
    assert title == "Funding & Ethics"
    assert paras[0] == "Paragraph 0 with <strong>bold</strong> and span."
    assert paras[5] == '<a href="https://example.com/r">link</a>'
    assert not any("Not part of the article" in p for p in paras)
    assert paras[-1].startswith("<em>A version of this commentary first appeared")


@pytest.fixture
def publish(cap, monkeypatch, tmp_path):
    """Run main() with Gmail and WordPress replaced by fakes."""
    image = tmp_path / "hero.jpg"
    image.write_bytes(b"jpg")
    calls = []
    state = {"image": str(image), "existing": []}

    monkeypatch.setattr(cap, "find_email", lambda subject, need_docx=True: {"id": "m1", "threadId": "t1"})
    monkeypatch.setattr(cap, "pick_attachments", lambda msg, d: ("doc.docx", state["image"]))
    monkeypatch.setattr(cap, "parse_docx", lambda path: ("Guest Column", ["Para one."]))
    monkeypatch.setattr(cap, "draft_reply", lambda msg, url: calls.append(("REPLY", url, None)))

    def get(url, auth, params):
        calls.append(("GET", url, params))
        return FakeHTTPResponse(state["existing"])

    def post(url, auth, json=None, data=None, headers=None):
        calls.append(("POST", url, json if json is not None else headers))
        if url.endswith("/posts"):
            return FakeHTTPResponse({"id": 99})
        if url.endswith("/media"):
            return FakeHTTPResponse({"id": 7})
        return FakeHTTPResponse({"link": "https://wp.example.test/guest-column/"})

    monkeypatch.setattr(cap.HTTP, "get", get)
    monkeypatch.setattr(cap.HTTP, "post", post)

    def run(*args):
        monkeypatch.setattr(sys, "argv", ["contributed_article_publish.py", "Guest column", *args])
        cap.main()
        return calls

    run.state = state
    return run


def test_publish_sequence_never_goes_live_without_image(publish):
    calls = publish()
    posts = [(c[1].split("/wp-json/wp/v2")[1], c[2]) for c in calls if c[0] == "POST"]
    assert posts[0] == ("/posts", {
        "title": "Guest Column", "status": "draft", "categories": [238, 86], "tags": [12],
        "content": ("<!-- wp:paragraph -->\n<p>The following was contributed by a guest author.</p>\n"
                    "<!-- /wp:paragraph -->\n\n<!-- wp:paragraph -->\n<p>Para one.</p>\n"
                    "<!-- /wp:paragraph -->")})
    assert posts[1][0] == "/media"
    assert posts[1][1]["Content-Type"] == "image/jpeg"
    assert posts[2] == ("/posts/99", {"featured_media": 7})
    assert posts[3] == ("/posts/99", {"status": "publish"})
    assert calls[-1] == ("REPLY", "https://wp.example.test/guest-column/", None)


def test_existing_article_with_same_title_is_not_republished(publish):
    publish.state["existing"] = [{"title": {"rendered": "Guest Column"}, "link": "https://wp.example.test/x/",
                                  "status": "publish"}]
    with pytest.raises(SystemExit, match="Already on LFJ"):
        publish()


def test_missing_image_blocks_publishing(publish):
    publish.state["image"] = None
    with pytest.raises(SystemExit, match="not publishing without a featured image"):
        publish()
