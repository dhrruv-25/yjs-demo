from pycrdt import Doc

from operations.helper import parse_md_str_to_html
from V2.operations.file_ops import doc_markdown


def render_md(md_str: str) -> str:
    return parse_md_str_to_html(md_str)


def render_doc(doc: Doc) -> str:
    return render_md(doc_markdown(doc))
