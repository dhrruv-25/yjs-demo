"""A local text editor. Documents are .txt files in ./documents."""

from pathlib import Path

from flask import Flask, abort, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent
DOCS = ROOT / "documents"

app = Flask(__name__)


def doc_file(name: str) -> Path:
    name = name.strip()
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or len(name) > 80
    ):
        abort(400, "Use a simple title (no slashes, 80 characters or fewer).")
    path = (DOCS / f"{name}.txt").resolve()
    if path.parent != DOCS.resolve():
        abort(400, "Use a simple title (no slashes, 80 characters or fewer).")
    return path


@app.errorhandler(400)
@app.errorhandler(404)
@app.errorhandler(409)
def api_error(err):
    return jsonify({"error": err.description}), err.code


@app.get("/")
def index():
    response = send_from_directory(ROOT / "static", "index.html")
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/api/docs")
def list_docs():
    DOCS.mkdir(exist_ok=True)
    docs = [
        {"name": path.stem, "chars": path.stat().st_size}
        for path in sorted(DOCS.glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    ]
    return jsonify(docs)


@app.get("/api/docs/<name>")
def read_doc(name):
    path = doc_file(name)
    if not path.is_file():
        abort(404, "Document not found.")
    return jsonify({"name": name, "content": path.read_text(encoding="utf-8")})


@app.put("/api/docs/<name>")
def save_doc(name):
    path = doc_file(name)
    body = request.get_json(silent=True) or {}
    content = body.get("content", "")
    if not isinstance(content, str):
        abort(400, "Content must be text.")

    previous = str(body.get("previous") or "").strip()
    old = doc_file(previous) if previous and previous != name else None
    if path.is_file() and old is not None:
        abort(409, "A document with that title already exists.")

    DOCS.mkdir(exist_ok=True)
    path.write_text(content, encoding="utf-8")
    if old is not None and old.is_file():
        old.unlink()
    return jsonify({"name": name, "content": content})


@app.delete("/api/docs/<name>")
def delete_doc(name):
    path = doc_file(name)
    if not path.is_file():
        abort(404, "Document not found.")
    path.unlink()
    return jsonify({"ok": True})


if __name__ == "__main__":
    DOCS.mkdir(exist_ok=True)
    print("Editor running at http://127.0.0.1:5050")
    app.run(host="127.0.0.1", port=5050, debug=True)
