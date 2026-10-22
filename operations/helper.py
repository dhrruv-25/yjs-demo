import markdownify as mdify
import markdown_it as mdit
import pycrdt as cr 
from pathlib import Path


md = mdit.MarkdownIt()

def handle_doc_changes(event: cr.TransactionEvent, remote_doc: cr.Doc):
    update: bytes = event.update

    remote_doc.apply_update(update)

def parse_doc_to_md_arr(doc: cr.Doc):
    return doc.get("markdown_arr", type=cr.Array)

def parse_md_arr_to_md_str(md_arr: list[str]):
    return "".join(md_arr)

def parse_doc_to_md_str(doc: cr.Doc):
    return parse_md_arr_to_md_str(parse_doc_to_md_arr(doc).to_py())

def parse_md_str_to_html(md_str: str):
    return md.render(md_str)

def parse_doc_to_html(doc: cr.Doc):
    return md.render(parse_md_arr_to_md_str(parse_doc_to_md_arr(doc)))

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

def save_doc_to_path(doc: cr.Doc, path: str):
    Path(path).write_bytes(doc.get_update())