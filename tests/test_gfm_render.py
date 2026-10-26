import pytest

from controller.markdown_session import MarkdownSession
from operations.helper import parse_doc_to_html, parse_md_str_to_doc, parse_md_str_to_html
from tests.conftest import write_doc


BOX = '<input class="task-list-item-checkbox" disabled="" type="checkbox">'
CHECKED = '<input class="task-list-item-checkbox" disabled="" type="checkbox" checked="">'

# Strikethrough, tables, and task lists are enabled on the renderer.
# A single newline is still a <br>. Footnotes are not a rule in this library.
GFM_CASES = [
    ("strikethrough", "~~nope~~", "<p><s>nope</s></p>\n"),
    ("strike words", "~~strike this~~", "<p><s>strike this</s></p>\n"),
    ("strike and bold", "~~gone~~ and **kept**", "<p><s>gone</s> and <strong>kept</strong></p>\n"),
    ("strike in a sentence", "see ~~this~~ here", "<p>see <s>this</s> here</p>\n"),
    ("strike wraps bold", "~~**both**~~", "<p><s><strong>both</strong></s></p>\n"),
    (
        "strike across a break",
        "~~a\nb~~",
        "<p><s>a<br />\nb</s></p>\n",
    ),
    (
        "strike outside code",
        "`~~nope~~` and ~~nope~~",
        "<p><code>~~nope~~</code> and <s>nope</s></p>\n",
    ),
    (
        "simple table",
        "| a | b |\n| --- | --- |\n| 1 | 2 |",
        "<table>\n<thead>\n<tr>\n<th>a</th>\n<th>b</th>\n</tr>\n</thead>\n"
        "<tbody>\n<tr>\n<td>1</td>\n<td>2</td>\n</tr>\n</tbody>\n</table>\n",
    ),
    (
        "aligned table",
        "| Left | Center | Right |\n| :--- | :---: | ---: |\n| a | b | c |",
        '<table>\n<thead>\n<tr>\n'
        '<th style="text-align:left">Left</th>\n'
        '<th style="text-align:center">Center</th>\n'
        '<th style="text-align:right">Right</th>\n'
        "</tr>\n</thead>\n<tbody>\n<tr>\n"
        '<td style="text-align:left">a</td>\n'
        '<td style="text-align:center">b</td>\n'
        '<td style="text-align:right">c</td>\n'
        "</tr>\n</tbody>\n</table>\n",
    ),
    (
        "table cell markdown",
        "| **b** | ~~s~~ |\n| --- | --- |\n| `c` | [a](https://example.com) |",
        "<table>\n<thead>\n<tr>\n<th><strong>b</strong></th>\n<th><s>s</s></th>\n</tr>\n</thead>\n"
        '<tbody>\n<tr>\n<td><code>c</code></td>\n<td><a href="https://example.com">a</a></td>\n'
        "</tr>\n</tbody>\n</table>\n",
    ),
    (
        "empty cell",
        "| a | b |\n| --- | --- |\n|  | 2 |",
        "<table>\n<thead>\n<tr>\n<th>a</th>\n<th>b</th>\n</tr>\n</thead>\n"
        "<tbody>\n<tr>\n<td></td>\n<td>2</td>\n</tr>\n</tbody>\n</table>\n",
    ),
    (
        "table then paragraph",
        "| a |\n| --- |\n| 1 |\n\nafter",
        "<table>\n<thead>\n<tr>\n<th>a</th>\n</tr>\n</thead>\n"
        "<tbody>\n<tr>\n<td>1</td>\n</tr>\n</tbody>\n</table>\n<p>after</p>\n",
    ),
    (
        "task list",
        "- [ ] todo\n- [x] done",
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{BOX} todo</li>\n'
        f'<li class="task-list-item">{CHECKED} done</li>\n'
        "</ul>\n",
    ),
    (
        "uppercase checkbox",
        "- [X] Done",
        f'<ul class="contains-task-list">\n<li class="task-list-item">{CHECKED} Done</li>\n</ul>\n',
    ),
    (
        "star checkbox",
        "* [ ] star",
        f'<ul class="contains-task-list">\n<li class="task-list-item">{BOX} star</li>\n</ul>\n',
    ),
    (
        "mixed task list",
        "- [ ] open\n- plain\n- [x] closed",
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{BOX} open</li>\n'
        "<li>plain</li>\n"
        f'<li class="task-list-item">{CHECKED} closed</li>\n'
        "</ul>\n",
    ),
    (
        "ordered task list",
        "1. [ ] one\n2. [x] two",
        '<ol class="contains-task-list">\n'
        f'<li class="task-list-item">{BOX} one</li>\n'
        f'<li class="task-list-item">{CHECKED} two</li>\n'
        "</ol>\n",
    ),
    (
        "nested task list",
        "- [ ] parent\n  - [x] child",
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{BOX} parent\n'
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{CHECKED} child</li>\n'
        "</ul>\n</li>\n</ul>\n",
    ),
    (
        "task in a quote",
        "> - [x] done",
        "<blockquote>\n"
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{CHECKED} done</li>\n'
        "</ul>\n</blockquote>\n",
    ),
    (
        "task with bold",
        "- [x] ** DO: **",
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{CHECKED} <strong>DO:</strong></li>\n'
        "</ul>\n",
    ),
    (
        "soft break stays a break",
        "A sentence.\nA soft line break above should join this line",
        "<p>A sentence.<br />\nA soft line break above should join this line</p>\n",
    ),
    (
        "two-space hard break",
        "A sentence.  \nA hard break stays on the next line",
        "<p>A sentence.<br />\nA hard break stays on the next line</p>\n",
    ),
    (
        "blank line is a new paragraph",
        "A sentence.\n\nA new paragraph.",
        "<p>A sentence.</p>\n<p>A new paragraph.</p>\n",
    ),
]

# Similar source that this renderer leaves alone.
LITERAL_CASES = [
    ("single tilde", "~nope~", "<p>~nope~</p>\n"),
    ("escaped strike", r"\~~nope~~", "<p>~~nope~~</p>\n"),
    ("unclosed strike", "~~nope", "<p>~~nope</p>\n"),
    ("spaced strike", "~~ nope ~~", "<p>~~ nope ~~</p>\n"),
    ("strike in inline code", "`~~nope~~`", "<p><code>~~nope~~</code></p>\n"),
    ("strike in a fence", "```\n~~nope~~\n```", "<pre><code>~~nope~~\n</code></pre>\n"),
    ("pipes without a separator", "| not | a | table |", "<p>| not | a | table |</p>\n"),
    (
        "table in a fence",
        "```\n| a | b |\n| --- | --- |\n| 1 | 2 |\n```",
        "<pre><code>| a | b |\n| --- | --- |\n| 1 | 2 |\n</code></pre>\n",
    ),
    ("checkbox in a paragraph", "not a [ ] list", "<p>not a [ ] list</p>\n"),
    ("checkbox needs a space", "- [x]done", "<ul>\n<li>[x]done</li>\n</ul>\n"),
    ("brackets are not a checkbox", "- [not] a task\n- item", "<ul>\n<li>[not] a task</li>\n<li>item</li>\n</ul>\n"),
    ("task in code", "`- [x] done`", "<p><code>- [x] done</code></p>\n"),
    ("footnote marker", "see [^1]", "<p>see [^1]</p>\n"),
    (
        "footnote definition",
        "[^1]: This is the footnote text.",
        "<p>[^1]: This is the footnote text.</p>\n",
    ),
    (
        "footnote pair",
        "see [^1]\n\n[^1]: This is the footnote text.",
        "<p>see [^1]</p>\n<p>[^1]: This is the footnote text.</p>\n",
    ),
    ("bare url", "https://example.com", "<p>https://example.com</p>\n"),
]


KITCHEN_SINK = """~~strikethrough~~

A sentence.
A soft line break above should join this line

- [x] Completed task
- [ ] Open task

| Left | Center | Right |
| :--- | :---: | ---: |
| a | b | c |

see [^1]

[^1]: This is the footnote text.
"""

KITCHEN_SINK_HTML = (
    "<p><s>strikethrough</s></p>\n"
    "<p>A sentence.<br />\nA soft line break above should join this line</p>\n"
    '<ul class="contains-task-list">\n'
    f'<li class="task-list-item">{CHECKED} Completed task</li>\n'
    f'<li class="task-list-item">{BOX} Open task</li>\n'
    "</ul>\n"
    "<table>\n<thead>\n<tr>\n"
    '<th style="text-align:left">Left</th>\n'
    '<th style="text-align:center">Center</th>\n'
    '<th style="text-align:right">Right</th>\n'
    "</tr>\n</thead>\n<tbody>\n<tr>\n"
    '<td style="text-align:left">a</td>\n'
    '<td style="text-align:center">b</td>\n'
    '<td style="text-align:right">c</td>\n'
    "</tr>\n</tbody>\n</table>\n"
    "<p>see [^1]</p>\n"
    "<p>[^1]: This is the footnote text.</p>\n"
)

CARET = '<span class="caret" data-sid="u" style="--c:#f7768e"></span>'


@pytest.mark.parametrize("name,source,expected", GFM_CASES, ids=[case[0] for case in GFM_CASES])
def test_gfm_syntax_renders(name, source, expected):
    assert parse_md_str_to_html(source) == expected
    assert parse_doc_to_html(parse_md_str_to_doc(source)) == expected


@pytest.mark.parametrize("name,source,expected", LITERAL_CASES, ids=[case[0] for case in LITERAL_CASES])
def test_similar_gfm_source_stays_literal(name, source, expected):
    assert parse_md_str_to_html(source) == expected
    assert parse_doc_to_html(parse_md_str_to_doc(source)) == expected


def test_a_url_definition_is_a_reference_link_not_a_footnote():
    """[^1]: https://... is a CommonMark link reference. It does not become a footnote."""
    html = parse_md_str_to_html("see [^1].\n\n[^1]: https://example.com")

    assert html == '<p>see <a href="https://example.com">^1</a>.</p>\n'
    assert "footnote" not in html
    assert "<sup>" not in html


def test_kitchen_sink_matches_the_enabled_rules():
    html = parse_md_str_to_html(KITCHEN_SINK)

    assert html == KITCHEN_SINK_HTML
    assert parse_doc_to_html(parse_md_str_to_doc(KITCHEN_SINK)) == KITCHEN_SINK_HTML
    assert "<s>strikethrough</s>" in html
    assert "<table>" in html
    assert "task-list-item-checkbox" in html
    assert "[^1]" in html
    assert "<br />" in html


def test_caret_stays_inside_strikethrough_table_and_task(docs_dir):
    strike = MarkdownSession(write_doc(docs_dir, text="~~ab~~"))
    strike.indexes = {"u": 3}
    assert strike._html_with_carets("~~ab~~") == f"<p><s>a{CARET}b</s></p>\n"

    task = MarkdownSession(write_doc(docs_dir, text="- [x] ab"))
    task.indexes = {"u": 7}
    assert task._html_with_carets("- [x] ab") == (
        '<ul class="contains-task-list">\n'
        f'<li class="task-list-item">{CHECKED} a{CARET}b</li>\n'
        "</ul>\n"
    )

    table = "| a |\n| --- |\n| b |"
    cell = MarkdownSession(write_doc(docs_dir, text=table))
    cell.indexes = {"u": 3}
    assert cell._html_with_carets(table) == (
        "<table>\n<thead>\n<tr>\n"
        f"<th>a{CARET}</th>\n"
        "</tr>\n</thead>\n<tbody>\n<tr>\n<td>b</td>\n</tr>\n</tbody>\n</table>\n"
    )
