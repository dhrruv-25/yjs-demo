import asyncio

import pytest

from controller.markdown_session import MarkdownSession
from operations.helper import parse_doc_to_html, parse_md_str_to_doc, parse_md_str_to_html
from tests.conftest import write_doc


# Markers may have spaces inside them. The preview closes those markers around
# the words. The source the editor stores is unchanged.
EMPHASIS_CASES = [
    ("hugging bold", "**Don't:**", "<p><strong>Don't:</strong></p>\n"),
    ("plan do", "** DO: **", "<p><strong>DO:</strong></p>\n"),
    ("plan don't", "**  Don't:  **", "<p><strong>Don't:</strong></p>\n"),
    ("plan problems", "** Problems **", "<p><strong>Problems</strong></p>\n"),
    ("space after opener only", "** Don't:**", "<p><strong>Don't:</strong></p>\n"),
    ("space before closer only", "**Don't: **", "<p><strong>Don't:</strong></p>\n"),
    ("tabs", "**\tDon't:\t**", "<p><strong>Don't:</strong></p>\n"),
    ("inner spaces stay", "** bold text **", "<p><strong>bold text</strong></p>\n"),
    ("unicode", "café ** naïve **", "<p>café <strong>naïve</strong></p>\n"),
    ("two phrases", "** Don't: ** more ** Problems **", "<p><strong>Don't:</strong> more <strong>Problems</strong></p>\n"),
    ("phrase in a sentence", "see ** Problems ** here and ** DO: ** too", "<p>see <strong>Problems</strong> here and <strong>DO:</strong> too</p>\n"),
    ("adjacent runs", "**a** ** b **", "<p><strong>a</strong> <strong>b</strong></p>\n"),
    ("odd marker left over", "** a ** ** b", "<p><strong>a</strong> ** b</p>\n"),
    ("bold number", "2 ** 3 ** 4", "<p>2 <strong>3</strong> 4</p>\n"),
    ("nested italic", "** bold *and* italic **", "<p><strong>bold <em>and</em> italic</strong></p>\n"),
    ("triple", "*** both ***", "<p><em><strong>both</strong></em></p>\n"),
    ("underscore", "__  Don't:  __", "<p><strong>Don't:</strong></p>\n"),
    ("triple underscore", "___ both ___", "<p><em><strong>both</strong></em></p>\n"),
    ("blockquote", "> ** Don't **", "<blockquote>\n<p><strong>Don't</strong></p>\n</blockquote>\n"),
    ("list item", "- ** Don't **", "<ul>\n<li><strong>Don't</strong></li>\n</ul>\n"),
    (
        "bold then list",
        "** DO: **\n- keep this a list",
        "<p><strong>DO:</strong></p>\n<ul>\n<li>keep this a list</li>\n</ul>\n",
    ),
    (
        "trailing spaces outside markers",
        "**DO:**  \n- item",
        "<p><strong>DO:</strong></p>\n<ul>\n<li>item</li>\n</ul>\n",
    ),
    (
        "problems then italic",
        "**Problems** \n\n1. An *enter* input",
        "<p><strong>Problems</strong></p>\n<ol>\n<li>An <em>enter</em> input</li>\n</ol>\n",
    ),
    ("hugging italic", "*enter*", "<p><em>enter</em></p>\n"),
    ("two hugging italics", "An *enter* and *enters* word", "<p>An <em>enter</em> and <em>enters</em> word</p>\n"),
]

# These look similar but must not be rewritten into emphasis.
LITERAL_CASES = [
    ("unclosed", "** only", "<p>** only</p>\n"),
    ("does not cross a break", "** Don't\n**", "<p>** Don't<br />\n**</p>\n"),
    ("marker on its own line", "**\nDon't\n**", "<p>**<br />\nDon't<br />\n**</p>\n"),
    ("empty markers are a rule", "** **", "<hr />\n"),
    ("wider empty markers", "**  **", "<hr />\n"),
    ("four stars are a rule", "****", "<hr />\n"),
    ("escaped opener", r"\** Don't **", "<p>** Don't **</p>\n"),
    ("single stars stay a list", "* enter *", "<ul>\n<li>enter *</li>\n</ul>\n"),
    ("multiplication", "2 * 3 * 4", "<p>2 * 3 * 4</p>\n"),
    ("list markers", "* item\n* item2", "<ul>\n<li>item</li>\n<li>item2</li>\n</ul>\n"),
    (
        "inline code is literal",
        "`**  Don't:  **` and **  Don't:  **",
        "<p><code>**  Don't:  **</code> and <strong>Don't:</strong></p>\n",
    ),
    (
        "fence is literal",
        "```\n**  Don't:  **\n```\n\n**  Don't:  **",
        "<pre><code>**  Don't:  **\n</code></pre>\n<p><strong>Don't:</strong></p>\n",
    ),
]

PLAN = """**DO:**
- Load SoT doc into session memory.

**  Don't:  **
- Load/dump docs into drive.

** Problems **

1. An *enter* input needs processing.
2. How to parse *enter* as a str.
"""

PLAN_HTML = (
    "<p><strong>DO:</strong></p>\n"
    "<ul>\n<li>Load SoT doc into session memory.</li>\n</ul>\n"
    "<p><strong>Don't:</strong></p>\n"
    "<ul>\n<li>Load/dump docs into drive.</li>\n</ul>\n"
    "<p><strong>Problems</strong></p>\n"
    "<ol>\n"
    "<li>An <em>enter</em> input needs processing.</li>\n"
    "<li>How to parse <em>enter</em> as a str.</li>\n"
    "</ol>\n"
)

CARET = '<span class="caret" data-sid="u" style="--c:#f7768e"></span>'


@pytest.mark.parametrize("name,source,expected", EMPHASIS_CASES, ids=[case[0] for case in EMPHASIS_CASES])
def test_spaced_emphasis_renders(name, source, expected):
    assert parse_md_str_to_html(source) == expected
    assert parse_doc_to_html(parse_md_str_to_doc(source)) == expected


@pytest.mark.parametrize("name,source,expected", LITERAL_CASES, ids=[case[0] for case in LITERAL_CASES])
def test_similar_markers_stay_literal(name, source, expected):
    assert parse_md_str_to_html(source) == expected
    assert parse_doc_to_html(parse_md_str_to_doc(source)) == expected


def test_plan_document_emphasis():
    assert parse_md_str_to_html(PLAN) == PLAN_HTML
    assert parse_doc_to_html(parse_md_str_to_doc(PLAN)) == PLAN_HTML


def test_caret_in_the_padding_stays_inside_the_bold(docs_dir):
    source = "**  Don't:  **"
    session = MarkdownSession(write_doc(docs_dir, text=source))
    session.indexes = {"u": 2}

    assert session._html_with_carets(source) == f"<p><strong>{CARET}Don't:</strong></p>\n"


def test_broadcast_renders_the_plan_emphasis(docs_dir):
    async def scenario():
        session = MarkdownSession(write_doc(docs_dir, text=PLAN))
        session.add_member("u")
        session.indexes["u"] = PLAN.index("Don't")

        state = await session.broadcast()

        assert state["markdown"] == PLAN
        assert "<strong>DO:</strong>" in state["html"]
        assert f"<strong>{CARET}Don't:</strong>" in state["html"]
        assert "<strong>Problems</strong>" in state["html"]
        assert "<em>enter</em>" in state["html"]
        assert "**" not in state["html"]

    asyncio.run(scenario())
