import asyncio

import pytest

from controller.markdown_session import MarkdownSession
from operations.helper import parse_doc_to_html, parse_md_str_to_doc, parse_md_str_to_html
from tests.conftest import write_doc


# CommonMark, with markdown-it's breaks option on, so a single newline is a <br>.
RENDER_CASES = [
    ("empty", "", ""),
    ("whitespace", "   ", ""),
    ("plain", "hello", "<p>hello</p>\n"),
    ("h1", "# Title", "<h1>Title</h1>\n"),
    ("h2", "## Title", "<h2>Title</h2>\n"),
    ("h6", "###### Deep", "<h6>Deep</h6>\n"),
    ("not a heading", "####### Not", "<p>####### Not</p>\n"),
    ("setext", "Title\n====", "<h1>Title</h1>\n"),
    ("emphasis", "*em* and _em_", "<p><em>em</em> and <em>em</em></p>\n"),
    ("strong", "**b** and __b__", "<p><strong>b</strong> and <strong>b</strong></p>\n"),
    ("spaced bold", "** DO: **", "<p><strong>DO:</strong></p>\n"),
    ("padded bold", "**  Don't:  **", "<p><strong>Don't:</strong></p>\n"),
    ("spaced words", "** Problems **", "<p><strong>Problems</strong></p>\n"),
    ("inner spaces stay", "** bold text **", "<p><strong>bold text</strong></p>\n"),
    ("spaced both", "*** both ***", "<p><em><strong>both</strong></em></p>\n"),
    ("spaced underscore", "__ Don't __", "<p><strong>Don't</strong></p>\n"),
    ("both", "***both***", "<p><em><strong>both</strong></em></p>\n"),
    (
        "nested emphasis",
        "this is **bold *and* italic**",
        "<p>this is <strong>bold <em>and</em> italic</strong></p>\n",
    ),
    ("unclosed", "**oops", "<p>**oops</p>\n"),
    ("escaped", r"\*not bold\*", "<p>*not bold*</p>\n"),
    ("inline code", "use `a * b`", "<p>use <code>a * b</code></p>\n"),
    ("fence", "```\nprint(1)\n```", "<pre><code>print(1)\n</code></pre>\n"),
    (
        "fence language",
        "```python\nx = 1\n```",
        '<pre><code class="language-python">x = 1\n</code></pre>\n',
    ),
    (
        "code hides markdown",
        "```\n**not bold**\n```",
        "<pre><code>**not bold**\n</code></pre>\n",
    ),
    ("inline code newline", "`a\nb`", "<p><code>a b</code></p>\n"),
    ("quote", "> quoted\n> still", "<blockquote>\n<p>quoted<br />\nstill</p>\n</blockquote>\n"),
    (
        "quote then paragraph",
        "> quote\n\npara",
        "<blockquote>\n<p>quote</p>\n</blockquote>\n<p>para</p>\n",
    ),
    ("ul", "* item\n* item2", "<ul>\n<li>item</li>\n<li>item2</li>\n</ul>\n"),
    ("ol", "1. one\n2. two", "<ol>\n<li>one</li>\n<li>two</li>\n</ol>\n"),
    (
        "nested list",
        "- a\n  - b\n- c",
        "<ul>\n<li>a\n<ul>\n<li>b</li>\n</ul>\n</li>\n<li>c</li>\n</ul>\n",
    ),
    (
        "loose list item",
        "1. one\n\n   more",
        "<ol>\n<li>\n<p>one</p>\n<p>more</p>\n</li>\n</ol>\n",
    ),
    ("hr", "---", "<hr />\n"),
    ("hr then strong", "---\n\n**a**", "<hr />\n<p><strong>a</strong></p>\n"),
    ("link", "[docs](https://example.com)", '<p><a href="https://example.com">docs</a></p>\n'),
    (
        "link title",
        '[a](u "title")',
        '<p><a href="u" title="title">a</a></p>\n',
    ),
    (
        "reference link",
        "[x][y]\n\n[y]: https://example.com",
        '<p><a href="https://example.com">x</a></p>\n',
    ),
    (
        "image",
        "![alt](https://example.com/a.png)",
        '<p><img src="https://example.com/a.png" alt="alt" /></p>\n',
    ),
    ("image title", '![x](u "t")', '<p><img src="u" alt="x" title="t" /></p>\n'),
    ("raw html", "line <em>raw</em>", "<p>line <em>raw</em></p>\n"),
    ("escape text", "a < b & c", "<p>a &lt; b &amp; c</p>\n"),
    ("unicode", "café **naïve**", "<p>café <strong>naïve</strong></p>\n"),
    ("single break", "a\nb", "<p>a<br />\nb</p>\n"),
    ("hard break", "a\\\nb", "<p>a<br />\nb</p>\n"),
    ("paragraphs", "para one\n\npara two", "<p>para one</p>\n<p>para two</p>\n"),
    ("break then paragraph", "a\nb\n\nc", "<p>a<br />\nb</p>\n<p>c</p>\n"),
    ("trailing newline", "hello\n", "<p>hello</p>\n"),
    (
        "mixed",
        "# T\n\nHello **world**\nnext line\n\n- a\n- b\n\n`code`",
        "<h1>T</h1>\n<p>Hello <strong>world</strong><br />\nnext line</p>\n<ul>\n<li>a</li>\n<li>b</li>\n</ul>\n<p><code>code</code></p>\n",
    ),
]

# A javascript: URL is left as source text.
UNSUPPORTED_CASES = [
    (
        "javascript link",
        "[a](javascript:alert(1))",
        "<p>[a](javascript:alert(1))</p>\n",
    ),
]


@pytest.mark.parametrize("name,source,expected", RENDER_CASES, ids=[case[0] for case in RENDER_CASES])
def test_markdown_renders_to_html(name, source, expected):
    assert parse_md_str_to_html(source) == expected


@pytest.mark.parametrize("name,source,expected", UNSUPPORTED_CASES, ids=[case[0] for case in UNSUPPORTED_CASES])
def test_non_commonmark_syntax_stays_literal(name, source, expected):
    assert parse_md_str_to_html(source) == expected


def test_spaced_markers_do_not_rewrite_code_or_single_stars():
    assert parse_md_str_to_html("`** DO: **`") == "<p><code>** DO: **</code></p>\n"
    assert parse_md_str_to_html("```\n** DO: **\n```") == "<pre><code>** DO: **\n</code></pre>\n"
    assert parse_md_str_to_html("2 * 3 * 4") == "<p>2 * 3 * 4</p>\n"
    assert parse_md_str_to_html("* item\n* item2") == "<ul>\n<li>item</li>\n<li>item2</li>\n</ul>\n"


@pytest.mark.parametrize("name,source,expected", RENDER_CASES, ids=[case[0] for case in RENDER_CASES])
def test_doc_render_matches_string_render(name, source, expected):
    doc = parse_md_str_to_doc(source)

    assert parse_doc_to_html(doc) == expected
    assert parse_doc_to_html(doc) == parse_md_str_to_html(source)


CARET = '<span class="caret" data-sid="{sid}" style="--c:{color}"></span>'
U = CARET.format(sid="u", color="#f7768e")
A = CARET.format(sid="a", color="#f7768e")
B = CARET.format(sid="b", color="#bb9af7")


@pytest.mark.parametrize(
    "source,indexes,expected",
    [
        ("**ab**", {"u": 3}, f"<p><strong>a{U}b</strong></p>\n"),
        ("**ab**", {"u": 0}, f"<p>{U}<strong>ab</strong></p>\n"),
        ("# Hi", {"u": 2}, f"<h1>{U}Hi</h1>\n"),
        ("- ab", {"u": 3}, f"<ul>\n<li>a{U}b</li>\n</ul>\n"),
        ("a\nb", {"u": 1}, f"<p>a{U}<br />\nb</p>\n"),
        ("hi", {"u": 2}, f"<p>hi{U}</p>\n"),
        ("hi", {"u": 0}, f"<p>{U}hi</p>\n"),
        ("hi", {"a": 1, "b": 1}, f"<p>h{A}{B}i</p>\n"),
        ("**  DO:  **", {"u": 4}, f"<p><strong>{U}DO:</strong></p>\n"),
    ],
)
def test_carets_stay_inside_the_rendered_markup(docs_dir, source, indexes, expected):
    session = MarkdownSession(write_doc(docs_dir, text=source))
    session.indexes = dict(indexes)

    assert session._html_with_carets(source) == expected


def test_caret_inside_code_is_escaped_as_text(docs_dir):
    """A caret span is HTML, so a code span treats it as source text."""
    session = MarkdownSession(write_doc(docs_dir, text="`ab`"))
    session.indexes = {"u": 2}

    html = session._html_with_carets("`ab`")

    assert "<span" not in html
    assert "&lt;span class=&quot;caret&quot;" in html
    assert html.startswith("<p><code>")


def test_broadcast_renders_a_mixed_document_with_a_caret(docs_dir):
    async def scenario():
        source = "# Notes\n\nHello **world**\nnext\n\n- one\n- two"
        session = MarkdownSession(write_doc(docs_dir, text=source))
        session.add_member("u")
        session.indexes["u"] = len("# Notes\n\nHello ")

        state = await session.broadcast()

        assert state["markdown"] == source
        assert state["html"] == (
            "<h1>Notes</h1>\n"
            f"<p>Hello {U}<strong>world</strong><br />\nnext</p>\n"
            "<ul>\n<li>one</li>\n<li>two</li>\n</ul>\n"
        )
        assert state["indexes"] == {"u": len("# Notes\n\nHello ")}

    asyncio.run(scenario())
