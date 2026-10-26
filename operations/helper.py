import re
import markdownify as mdify
import markdown_it as mdit
import pycrdt as cr 
from pathlib import Path


md = mdit.MarkdownIt(options_update={"breaks": True, "tasklists": True})
md.enable(["table", "strikethrough"])


markdown = cr.Doc()
markdown['markdown_arr'] = cr.Array()
markdown['md_title'] = cr.Array()


def handle_doc_changes(event: cr.TransactionEvent, remote_doc: cr.Doc):
    update: bytes = event.update

    remote_doc.apply_update(update)


def parse_doc_to_md_arr(doc: cr.Doc):
    return doc.get("markdown_arr", type=cr.Array)


def parse_md_arr_to_md_str(md_arr: list[str]):
    return "".join(md_arr)


def parse_doc_to_md_str(doc: cr.Doc):
    return parse_md_arr_to_md_str(parse_doc_to_md_arr(doc).to_py())


_CODE_REGION = re.compile(r"(```.*?```|``[^`\n]*``|`[^`\n]*`)", re.DOTALL)
_EMPHASIS_MARKERS = ("***", "___", "**", "__")
_CARET_SPAN = r"<span\b[^>]*></span>"


def _tighten_emphasis(text: str) -> str:
    """Let ** text ** render as bold. CommonMark ignores markers that touch whitespace."""
    parts = _CODE_REGION.split(text)
    return "".join(part if part.startswith("`") else _tighten_emphasis_segment(part) for part in parts)


def _marker_indexes(text: str, marker: str) -> list[int]:
    """Indexes of marker runs that are not part of a longer run and are not escaped."""
    size = len(marker)
    char = marker[0]
    found = []
    i = 0
    while i <= len(text) - size:
        escaped = i > 0 and text[i - 1] == "\\"
        longer = (i > 0 and text[i - 1] == char) or (i + size < len(text) and text[i + size] == char)
        if text.startswith(marker, i) and not escaped and not longer:
            found.append(i)
            i += size
        else:
            i += 1
    return found


def _peel_emphasis(inner: str) -> tuple[str, str, str, bool]:
    """Drop spaces beside the markers. Keep a cursor span that was sitting in that space."""
    span = re.compile(_CARET_SPAN)
    removed = False
    i = 0
    leading = []
    while i < len(inner):
        if inner[i] in " \t":
            removed = True
            i += 1
            continue
        match = span.match(inner, i)
        if match:
            leading.append(match.group(0))
            i = match.end()
            continue
        break
    j = len(inner)
    trailing = []
    while j > i:
        if inner[j - 1] in " \t":
            removed = True
            j -= 1
            continue
        match = span.search(inner, i, j)
        if match and match.end() == j:
            trailing.append(match.group(0))
            j = match.start()
            continue
        break
    trailing.reverse()
    return "".join(leading), inner[i:j], "".join(trailing), removed


def _tighten_marker(text: str, marker: str) -> str:
    indexes = _marker_indexes(text, marker)
    for start, end in reversed(list(zip(indexes[0::2], indexes[1::2]))):
        inner = text[start + len(marker):end]
        if "\n" in inner:
            continue
        leading, content, trailing, removed = _peel_emphasis(inner)
        if not removed or not content.strip(" \t"):
            continue
        text = text[:start] + marker + leading + content + trailing + marker + text[end + len(marker):]
    return text


def _tighten_emphasis_segment(text: str) -> str:
    for marker in _EMPHASIS_MARKERS:
        text = _tighten_marker(text, marker)
    return text


def parse_md_str_to_html(md_str: str):
    return md.render(_tighten_emphasis(md_str))


def parse_doc_to_html(doc: cr.Doc):
    return parse_md_str_to_html(parse_md_arr_to_md_str(parse_doc_to_md_arr(doc)))


def parse_html_to_md(html: str):
    return mdify.markdownify(html)


def parse_md_str_to_doc(md: str):
    assert isinstance(md, str)
    doc = cr.Doc()
    doc['markdown_arr'] = cr.Array(md)
    return doc


def parse_html_to_doc(html: str):
    assert isinstance(html, str)
    md = parse_html_to_md(html)
    return parse_md_str_to_doc(md)


def get_doc_copy_from_path(path: str):
    update : bytes = Path(path).read_bytes()
    doc = cr.Doc()
    doc.apply_update(update)
    return doc


def get_title_from_path(path: str):
    return parse_md_arr_to_md_str(get_doc_copy_from_path(path).get("md_title", type=cr.Array).to_py())


def set_title_to_doc_from_path(path: str, title: str):
    doc = get_doc_copy_from_path(path)
    arr = doc.get("md_title", type=cr.Array)
    with doc.transaction():
        arr.clear()
        if title:
            arr.extend(list(title))
    save_doc_to_path(doc, path)


def save_doc_to_path(doc: cr.Doc, path: str):
    Path(path).write_bytes(doc.get_update())


def delete_doc_from_path(path: str):
    Path(path).unlink(missing_ok=True)


def create_doc_from_path(path: str):
    save_doc_to_path(markdown, path)
