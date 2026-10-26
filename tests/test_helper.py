from pathlib import Path

import pycrdt as cr
import pytest

from operations.helper import (
    create_doc_from_path,
    delete_doc_from_path,
    get_doc_copy_from_path,
    get_title_from_path,
    handle_doc_changes,
    parse_doc_to_html,
    parse_doc_to_md_arr,
    parse_doc_to_md_str,
    parse_html_to_doc,
    parse_html_to_md,
    parse_md_arr_to_md_str,
    parse_md_str_to_doc,
    parse_md_str_to_html,
    save_doc_to_path,
    set_title_to_doc_from_path,
)


def test_markdown_string_roundtrip_splits_into_characters():
    doc = parse_md_str_to_doc("hello **world**")

    assert parse_doc_to_md_arr(doc).to_py() == list("hello **world**")
    assert parse_doc_to_md_str(doc) == "hello **world**"
    assert parse_md_arr_to_md_str(["a", "b", "c"]) == "abc"


def test_markdown_renders_emphasis_lists_and_single_newlines():
    assert parse_md_str_to_html("hello **world**") == "<p>hello <strong>world</strong></p>\n"
    assert parse_md_str_to_html("a\nb") == "<p>a<br />\nb</p>\n"
    assert parse_md_str_to_html("# T\n\npara") == "<h1>T</h1>\n<p>para</p>\n"
    assert parse_md_str_to_html("* item\n* item2") == "<ul>\n<li>item</li>\n<li>item2</li>\n</ul>\n"
    assert parse_md_str_to_html("") == ""


def test_doc_html_matches_string_render():
    doc = parse_md_str_to_doc("hello **world**")

    assert parse_doc_to_html(doc) == parse_md_str_to_html(parse_doc_to_md_str(doc))


def test_html_converts_back_to_markdown_and_a_doc():
    html = "<p>hello <strong>world</strong></p>"

    assert parse_html_to_md(html) == "hello **world**"
    assert parse_doc_to_md_str(parse_html_to_doc("<p>hello</p>")) == "hello"


def test_parse_rejects_non_strings():
    with pytest.raises(AssertionError):
        parse_md_str_to_doc(None)
    with pytest.raises(AssertionError):
        parse_html_to_doc(None)


def test_save_and_load_roundtrip(tmp_path):
    path = tmp_path / "note.crdt"
    save_doc_to_path(parse_md_str_to_doc("saved text"), path)

    loaded = get_doc_copy_from_path(path)

    assert parse_doc_to_md_str(loaded) == "saved text"
    assert isinstance(path.read_bytes(), bytes)


def test_title_roundtrip_keeps_body(tmp_path):
    path = tmp_path / "note.crdt"
    save_doc_to_path(parse_md_str_to_doc("body"), path)

    set_title_to_doc_from_path(path, "My Title")
    set_title_to_doc_from_path(path, "Renamed")

    assert get_title_from_path(path) == "Renamed"
    assert parse_doc_to_md_str(get_doc_copy_from_path(path)) == "body"


def test_create_doc_starts_empty_and_delete_removes_it(tmp_path):
    path = tmp_path / "new.crdt"

    create_doc_from_path(path)

    assert path.is_file()
    assert parse_doc_to_md_str(get_doc_copy_from_path(path)) == ""
    assert get_title_from_path(path) == ""

    delete_doc_from_path(path)
    delete_doc_from_path(tmp_path / "missing.crdt")

    assert not path.exists()


def test_handle_doc_changes_copies_update_onto_remote_doc():
    source = cr.Doc()
    remote = cr.Doc()
    source.observe(lambda event: handle_doc_changes(event, remote))

    source["markdown_arr"] = cr.Array(list("abc"))

    assert parse_doc_to_md_str(remote) == "abc"


def test_get_doc_copy_from_path_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        get_doc_copy_from_path(Path(tmp_path / "nope.crdt"))
