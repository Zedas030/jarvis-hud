"""
Local document search: full-text index over the shared folders, answers with
exact source references (file and position) — nothing leaves the PC to build it.

Folders: JARVIS_FILE_ROOTS (Desktop, Documents, Downloads) plus JARVIS_DOC_ROOTS
(e.g. a folder with manuals). Formats: text, Markdown, CSV, logs, code, Word
(.docx) — and PDF once the optional package `pypdf` is installed (otherwise PDFs
are skipped and the status says so).

Index: SQLite FTS5 in logs/docindex.sqlite, chunks of about 1,200 characters,
updated incrementally (only files whose size or time changed), at most
JARVIS_DOC_MAX_FILES files and JARVIS_DOC_MAX_MB per file. Work files are never
indexed (privacy.py). Results are document content — untrusted data for the
model (policy.UNTRUSTED), never instructions.
"""
import os
import pathlib
import re
import sqlite3
import threading
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]      # the repository (this file: jarvis/<group>/)
DB = pathlib.Path(os.environ.get("JARVIS_DOC_INDEX", ROOT / "logs" / "docindex.sqlite"))
EXTRA = [pathlib.Path(os.path.expandvars(os.path.expanduser(p.strip())))
         for p in os.environ.get("JARVIS_DOC_ROOTS", "").split(",") if p.strip()]
MAX_FILES = int(os.environ.get("JARVIS_DOC_MAX_FILES", "5000"))
MAX_MB = float(os.environ.get("JARVIS_DOC_MAX_MB", "20"))
CHUNK = 1200
TEXT_EXT = {".txt", ".md", ".csv", ".log", ".json", ".py", ".ps1", ".ini", ".yaml", ".yml", ".xml", ".html", ".docx"}
_LOCK = threading.Lock()


def roots():
    import desktop
    return list(desktop.FILE_ROOTS) + EXTRA


def pdf_available():
    try:
        import pypdf  # noqa: F401
        return True
    except ImportError:
        return False


def _conn():
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB, timeout=10)
    c.execute("create table if not exists files(path text primary key, size int, mtime real, chunks int)")
    c.execute("create virtual table if not exists chunks using fts5(path unindexed, pos unindexed, body, "
              "tokenize='unicode61 remove_diacritics 2')")
    return c


def _read(path):
    """[(position label, text)] — page numbers for PDF, character offsets otherwise."""
    ext = path.suffix.lower()
    if ext == ".pdf":
        import pypdf
        reader = pypdf.PdfReader(str(path))
        return [(f"page {i + 1}", page.extract_text() or "") for i, page in enumerate(reader.pages)]
    if ext == ".docx":
        import desktop
        text = desktop.read(str(path), max_chars=10**7)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
    return [("", text)]


def _chunks(parts):
    for label, text in parts:
        text = re.sub(r"[ \t]+", " ", text)
        for i in range(0, len(text), CHUNK):
            piece = text[i:i + CHUNK].strip()
            if piece:
                yield (label or f"character {i}"), piece


def index(max_files=None, deadline_s=120):
    """Incremental update. Returns (indexed, removed, skipped)."""
    import privacy
    exts = TEXT_EXT | ({".pdf"} if pdf_available() else set())
    max_files = max_files or MAX_FILES
    seen, indexed, skipped = set(), 0, 0
    end = time.time() + deadline_s
    with _LOCK:
        c = _conn()
        known = {p: (s, m) for p, s, m in c.execute("select path, size, mtime from files")}
        for root in roots():
            if not root.exists():
                continue
            for dirpath, dirnames, filenames in os.walk(root):
                dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in
                               {"node_modules", "__pycache__", ".git", "AppData", "venv", ".venv"}]
                for f in filenames:
                    p = pathlib.Path(dirpath) / f
                    if p.suffix.lower() not in exts or len(seen) >= max_files or time.time() > end:
                        continue
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    key = str(p)
                    if st.st_size > MAX_MB * 2**20 or privacy.is_work(f, dirpath):
                        skipped += 1
                        continue
                    seen.add(key)
                    if known.get(key) == (st.st_size, st.st_mtime):
                        continue
                    try:
                        parts = _read(p)
                    except Exception:  # noqa: BLE001 — a broken file must not stop the index
                        skipped += 1
                        continue
                    if privacy.is_work(" ".join(t[:2000] for _, t in parts[:1])):
                        skipped += 1
                        continue
                    c.execute("delete from chunks where path = ?", (key,))
                    rows = list(_chunks(parts))
                    c.executemany("insert into chunks(path, pos, body) values (?, ?, ?)",
                                  [(key, pos, body) for pos, body in rows])
                    c.execute("insert or replace into files values (?, ?, ?, ?)", (key, st.st_size, st.st_mtime, len(rows)))
                    indexed += 1
        removed = [p for p in known if p not in seen and time.time() <= end]
        for p in removed:
            c.execute("delete from chunks where path = ?", (p,))
            c.execute("delete from files where path = ?", (p,))
        c.commit()
        c.close()
    return indexed, len(removed), skipped


def _fts_query(q):
    words = [w for w in re.findall(r"\w+", (q or "").lower()) if len(w) > 2]
    return " ".join(f'"{w}"*' for w in words[:8])


def search(query, limit=6):
    """Text: best passages with file and position."""
    q = _fts_query(query)
    if not q:
        return "What should I search the documents for?"
    if not DB.exists():
        return "The document index is still empty — it is being built in the background."
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute("select path, pos, snippet(chunks, 2, '»', '«', '…', 24) from chunks "
                             "where chunks match ? order by rank limit ?", (q, max(1, min(limit, 10)))).fetchall()
        except sqlite3.OperationalError as e:
            return f"Search failed: {e}"
        finally:
            c.close()
    if not rows:
        return f"Nothing found for “{query}” in the documents."
    return "\n".join(f"[{i + 1}] {path} ({pos}): {snip}" for i, (path, pos, snip) in enumerate(rows))


def status():
    if not DB.exists():
        return "Index not built yet."
    with _LOCK:
        c = _conn()
        files, chunks = c.execute("select count(*), coalesce(sum(chunks), 0) from files").fetchone()
        c.close()
    return (f"{files} documents, {chunks} passages indexed in {', '.join(str(r) for r in roots())}. "
            + ("PDFs are read." if pdf_available() else "PDFs are skipped (package pypdf not installed)."))


def start():
    if os.environ.get("JARVIS_DOCSEARCH", "1").strip() in ("0", "false", "no", "off"):
        return
    import background
    background.loop("docsearch", 3600, lambda: index())
