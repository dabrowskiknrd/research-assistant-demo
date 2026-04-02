"""
Gradio UI for the Research Assistant RAG — Modal (Volumes variant)
==================================================================

Run locally (calls Modal functions remotely):
    python src/agent_rag/gradio_app.py

The app exposes four tabs:
  • Upload PDFs   — push local data/sources/pdfs/ folder into the Modal Volume.
                    Runs locally via the Modal SDK; no container needed.
  • Ingest        — index Volume PDFs into the Gemini FileSearchStore.
                    Runs as a remote Modal function.
  • Query         — ask questions, optionally restricted to a subset of books.
                    Runs as a remote Modal function.
  • Purge Store   — delete all files and the FileSearchStore from Gemini.
                    Runs as a remote Modal function (opt-in only).

Prerequisites
-------------
• Modal credentials configured (`modal setup`)
• .env file in the project root containing GEMINI_API_KEY=<your-key>
• PDFs placed in data/sources/pdfs/

No prior deployment needed — ingest/query/purge are invoked via `modal run`
as subprocesses, exactly like the CLI. Upload runs locally against the Volume.
"""

import asyncio
import html as _html
import os
import re
import subprocess
import sys
from pathlib import Path

import modal
import gradio as gr

# ---------------------------------------------------------------------------
# Paths and Modal resources
# ---------------------------------------------------------------------------

# Local directory where source PDFs/Markdown files live
SOURCES_DIR = Path(__file__).parent.parent.parent / "data" / "sources" / "pdfs"
SUPPORTED_EXTENSIONS = (".pdf", ".md")
APP_PY = Path(__file__).parent / "app.py"
LIBRARIAN_ASSISTANT_APP = Path(__file__).parent.parent / "agent_librarian_assistant" / "app.py"
PROJECT_ROOT = Path(__file__).parent.parent.parent

# Persistent Volume — accessed directly from local code for uploads (no container needed)
pdf_volume = modal.Volume.from_name("research-assistant-rag-pdfs", create_if_missing=True)


def _run_modal_action(*args: str) -> tuple[str, bool]:
    """Run ``modal run app.py <args>`` as a subprocess.

    Returns ``(stdout, success)`` where *success* is True when exit code is 0.
    Modal's own status/log lines go to stderr; the local_entrypoint print()
    calls go to stdout, so stdout is clean application output.
    """
    cmd = [sys.executable, "-m", "modal", "run", str(APP_PY), *args]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    out = result.stdout.strip()
    if result.returncode != 0:
        # Include stderr to surface Modal errors
        err = result.stderr.strip()
        return (f"{out}\n{err}" if out else err), False
    return out, True


def _stream_modal_action(*args: str):
    """Run ``modal run app.py <args>`` and yield output lines as they arrive.

    Merges stdout and stderr into a single stream so container log lines
    (stderr) and local_entrypoint print() output (stdout) both appear live.
    Yields ``(accumulated_text, success_so_far)``; final yield has the
    definitive success flag based on the process exit code.
    """
    cmd = [sys.executable, "-m", "modal", "run", str(APP_PY), *args]
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # merge stderr into stdout for live display
        text=True,
        cwd=str(PROJECT_ROOT),
        bufsize=1,  # line-buffered
    )
    accumulated: list[str] = []
    for line in proc.stdout:
        accumulated.append(line.rstrip())
        yield "\n".join(accumulated), True
    proc.wait()
    yield "\n".join(accumulated), proc.returncode == 0


# ---------------------------------------------------------------------------
# Helper: list files in the Modal Volume
# ---------------------------------------------------------------------------

async def _list_volume_files() -> list[str]:
    """Return the sorted list of filenames currently in the PDF Volume."""
    try:
        return sorted(entry.path.lstrip("/") for entry in await pdf_volume.listdir.aio("/"))
    except Exception:
        return []


def _enumerate_books(names: list[str]) -> list[tuple[str, str]]:
    """Convert a sorted filename list to (label, value) tuples for CheckboxGroup.

    The label shows a 1-based index: ``"1. Book Title.pdf"``; the value
    remains the plain filename so selection logic is unchanged.
    """
    return [(f"{i + 1}. {name}", name) for i, name in enumerate(names)]


# ---------------------------------------------------------------------------
# Tab handlers
# ---------------------------------------------------------------------------


async def run_upload_pdfs(selected_books: list[str]):
    """Push files from the local sources dir into the Modal Volume.

    If ``selected_books`` is non-empty, only those files are uploaded.
    """
    try:
        all_files = sorted(
            f for f in SOURCES_DIR.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        if not all_files:
            return (
                f"⚠️  No PDF or Markdown files found in {SOURCES_DIR}\n"
                "Add files and try again."
            ), await _list_volume_files()

        if selected_books:
            lower = {b.lower() for b in selected_books}
            doc_files = [f for f in all_files if f.name.lower() in lower]
        else:
            doc_files = all_files

        if not doc_files:
            return "⚠️  None of the selected files exist locally.", await _list_volume_files()

        names = []
        with pdf_volume.batch_upload(force=True) as batch:
            for pdf_path in doc_files:
                batch.put_file(str(pdf_path), f"/{pdf_path.name}")
                names.append(pdf_path.name)

        status = (
            f"✅  Uploaded {len(names)} file(s) to Modal Volume:\n"
            + "\n".join(f"  • {n}" for n in names)
        )
        return status, await _list_volume_files()
    except Exception as exc:
        return f"❌  Error: {exc}", await _list_volume_files()


def _is_relevant_ingest_line(line: str) -> bool:
    """Return True for meaningful ingest/store progress lines; skip Modal boilerplate."""
    stripped = line.strip()
    return any(stripped.startswith(tag) for tag in ("[ingest]", "[store]", "[done]", "[purge]"))


def run_ingest(selected_books: list[str]):
    """Stream ingest progress, showing only meaningful lines newest-first."""
    args = ["--action", "ingest"]
    if selected_books:
        args += ["--books", "|".join(selected_books)]

    relevant: list[str] = []
    last_text, success = "", True
    for last_text, success in _stream_modal_action(*args):
        # last_text is the full accumulated raw output — extract the last new line
        last_line = last_text.rsplit("\n", 1)[-1].strip()
        if last_line and _is_relevant_ingest_line(last_line):
            relevant.insert(0, last_line)  # newest at top
            yield "\n".join(relevant)

    if not success:
        relevant.insert(0, "❌  Ingest failed — check terminal for details.")
        yield "\n".join(relevant)
    elif relevant:
        # Ensure final [done] line is shown
        final_line = last_text.rsplit("\n", 1)[-1].strip()
        if final_line and _is_relevant_ingest_line(final_line) and final_line not in relevant:
            relevant.insert(0, final_line)
        yield "\n".join(relevant)


def run_query(question: str, selected_books: list[str]):
    """Invoke ``modal run app.py --action query`` as a subprocess."""
    if not question.strip():
        return "Please enter a question."
    args = ["--action", "query", "--question", question]
    if selected_books:
        args += ["--books", "|".join(selected_books)]
    out, success = _run_modal_action(*args)
    if not success:
        return f"❌  Error:\n{out}"
    # local_entrypoint prints "\n[answer]\n<text>" — return just the answer
    if "[answer]" in out:
        return out.split("[answer]", 1)[1].strip()
    return out


def run_purge():
    """Invoke ``modal run app.py --action purge_store`` as a subprocess."""
    out, success = _run_modal_action("--action", "purge_store")
    if not success:
        return f"❌  Error:\n{out}", gr.CheckboxGroup(choices=[], value=[])
    return f"✅  {out}", gr.CheckboxGroup(choices=[], value=[])


def run_remove_files(selected_files: list[str]):
    """Invoke ``modal run app.py --action remove_files`` for selected files."""
    if not selected_files:
        return "⚠️  No files selected. Check the boxes next to the files you want to remove."
    args = ["--action", "remove_files", "--books", "|".join(selected_files)]
    out, success = _run_modal_action(*args)
    if not success:
        return f"❌  Error:\n{out}"
    lines = [l for l in out.splitlines() if l.strip().startswith("[remove]") or l.strip().startswith("[done]")]
    return "✅  " + "\n".join(lines) if lines else f"✅  {out}"


async def refresh_purge_status():
    """Fetch current store contents for the Purge tab CheckboxGroup."""
    ingested = _get_ingested_filenames()
    choices = _enumerate_books(ingested)
    label = f"Select files to remove — {len(ingested)} file(s) in Gemini Store"
    return gr.CheckboxGroup(
        choices=choices,
        value=[],
        label=label,
        elem_classes=["books-list"],
    )


def run_list_store():
    """Invoke ``modal run app.py --action list_store`` and return a clean file list."""
    out, success = _run_modal_action("--action", "list_store")
    if not success:
        return f"❌  Error:\n{out}"
    lines = [l for l in out.splitlines() if l.strip().startswith("[list_store]")]
    return "\n".join(lines) if lines else "(no output)"


def _get_ingested_filenames() -> list[str]:
    """Return filenames currently indexed in the Gemini FileSearchStore.

    Parses ``  • <filename>`` lines from the ``list_store`` local entrypoint output.
    Returns an empty list if the store is unreachable or empty.
    """
    out, success = _run_modal_action("--action", "list_store")
    if not success:
        return []
    names = []
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("• "):
            fname = stripped[2:].strip()
            if fname:
                names.append(fname)
    return sorted(names)


async def refresh_ingest_status():
    """Fetch store contents and split Volume files into ingested / pending groups."""
    volume_files = await _list_volume_files()
    ingested = _get_ingested_filenames()
    ingested_lower = {f.lower() for f in ingested}
    pending = [f for f in volume_files if f.lower() not in ingested_lower]

    ingested_choices = [
        (f"✅ {i + 1}. {name}", name) for i, name in enumerate(sorted(ingested))
    ]
    return (
        gr.CheckboxGroup(
            choices=ingested_choices,
            value=[name for _, name in ingested_choices],
            interactive=False,
            label=f"Already ingested — {len(ingested)} file(s) in Gemini Store",
            elem_classes=["books-list"],
        ),
        gr.CheckboxGroup(
            choices=_enumerate_books(pending),
            value=[],
            label=f"Select books to ingest — {len(pending)} not yet in store (leave empty = ingest all)",
            elem_classes=["books-list"],
        ),
    )


# ---------------------------------------------------------------------------
# Librarian: invoke agent_librarian and auto-select matching Volume files
# ---------------------------------------------------------------------------

def _match_titles_to_volume(titles: list[str], volume_files: list[str]) -> list[str]:
    """Match DB-returned book titles to Volume filenames.

    For each title, a Volume file matches when at least 2 significant words
    (>3 chars) from the title appear in the filename (case-insensitive).
    """
    matched: list[str] = []
    for title in titles:
        words = [w.lower() for w in title.split() if len(w) > 3]
        if not words:
            continue
        for fname in volume_files:
            fname_lower = fname.lower()
            hits = sum(1 for w in words if w in fname_lower)
            if hits >= min(2, len(words)):
                if fname not in matched:
                    matched.append(fname)
                break
    return matched


async def _run_librarian_agent(question: str) -> tuple[str, list[str]]:
    """Run the librarian agent and return (answer_markdown, db_titles_found).

    Hooks into every search_books tool result to collect all titles the agent
    found, so they can be matched against Volume filenames afterwards.
    """
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv(raise_error_if_not_found=False))

    from google.genai import Client, types as gtypes
    from agent_generic.agent import Agent
    from agent_generic.state import AgentContext, RunConfig, RunState
    from agent_librarian.instructions import LIBRARIAN_INSTRUCTION
    from tools.books_db_search import SEARCH_BOOKS_TOOL
    from tools.pdf_paths import (
        LIST_CONVERTED_FILES_TOOL,
        LIST_SOURCE_PDFS_TOOL,
        LIST_UNPROCESSED_PDFS_TOOL,
    )
    from tools.abstract import SearchBooksMetadata

    client = Client()
    context = AgentContext()
    collected_titles: list[str] = []

    async def _collect_books(call, result, config, state, context):
        if isinstance(result.metadata, SearchBooksMetadata):
            for t in result.metadata.titles:
                if t not in collected_titles:
                    collected_titles.append(t)

    agent = Agent(
        client=client,
        config=RunConfig(model="gemini-3.1-pro-preview", thinking_level="LOW", max_iterations=10),
        state=RunState(mode="execute"),
        context=context,
        plan_tools=[],
        execute_tools=[
            SEARCH_BOOKS_TOOL,
            LIST_SOURCE_PDFS_TOOL,
            LIST_CONVERTED_FILES_TOOL,
            LIST_UNPROCESSED_PDFS_TOOL,
        ],
        plan_system_instruction=LIBRARIAN_INSTRUCTION,
        execute_system_instruction=LIBRARIAN_INSTRUCTION,
    )
    agent.on("tool_result", _collect_books)

    contents = [gtypes.UserContent(parts=[gtypes.Part.from_text(text=question)])]
    final_message = await agent.run_until_idle(contents)

    text_parts = [part.text for part in final_message.parts if part.text]
    return "\n\n".join(text_parts), collected_titles


async def run_librarian_suggest(question: str):
    """Invoke the librarian agent and auto-select matching books in the Volume."""
    volume_files = await _list_volume_files()
    empty_cb = gr.CheckboxGroup(choices=_enumerate_books(volume_files), value=[], elem_classes=["books-list"])

    if not question.strip():
        return empty_cb, "Please enter a question first."

    try:
        answer_text, db_titles = await _run_librarian_agent(question)
    except Exception as exc:
        return empty_cb, f"❌  Librarian agent error: {exc}"

    volume_files = await _list_volume_files()
    matched = _match_titles_to_volume(db_titles, volume_files)

    suffix_lines = ["\n\n---"]
    if matched:
        suffix_lines.append(
            f"**{len(matched)} book(s) auto-selected below** — adjust if needed, then click **Ask**."
        )
    else:
        suffix_lines.append(
            "⚠️  None of the recommended books were found in the Volume. "
            "Upload and ingest them first."
        )

    return (
        gr.CheckboxGroup(choices=_enumerate_books(volume_files), value=matched, elem_classes=["books-list"]),
        answer_text + "\n".join(suffix_lines),
    )


async def refresh_volume_books():
    """Return the current book list from the Volume for CheckboxGroup updates."""
    books = await _list_volume_files()
    return (
        gr.CheckboxGroup(choices=books, value=[]),
        gr.CheckboxGroup(choices=books, value=[]),
    )


async def refresh_ingest_books():
    """Refresh the ingest-tab book list from the Volume."""
    return gr.CheckboxGroup(choices=_enumerate_books(await _list_volume_files()), value=[], elem_classes=["books-list"])


async def refresh_query_books():
    """Refresh the query-tab book list from the Volume."""
    return gr.CheckboxGroup(choices=_enumerate_books(await _list_volume_files()), value=[], elem_classes=["books-list"])


def refresh_local_books():
    """Return the local file list for the Upload tab CheckboxGroup."""
    try:
        files = sorted(
            f.name for f in SOURCES_DIR.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    except Exception:
        files = []
    return gr.CheckboxGroup(choices=_enumerate_books(files), value=[], elem_classes=["books-list"])


# ---------------------------------------------------------------------------
# Research Books: run agent_librarian_assistant for Volume files
# ---------------------------------------------------------------------------

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\x1b[()][A-Z0-9]?|\r|\x0f|\x0e")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _split_by_db_status(volume_files: list[str]) -> tuple[list[str], list[str]]:
    """Split volume filenames into (in_db, not_in_db) by matching parsed titles against books.db."""
    import sqlite3

    db_titles_lower: set[str] = set()
    if DB_PATH.exists():
        conn = sqlite3.connect(DB_PATH)
        try:
            for row in conn.execute("SELECT lower(title) FROM books"):
                db_titles_lower.add(row[0])
        finally:
            conn.close()

    try:
        from agent_librarian_assistant.app import parse_path_input as _parse_path
    except ImportError:
        return [], list(volume_files)

    in_db: list[str] = []
    not_in_db: list[str] = []
    for fname in volume_files:
        parsed = _parse_path(fname)
        found = False
        if parsed and db_titles_lower:
            t = parsed.title.lower()
            if t in db_titles_lower:
                found = True
            else:
                for dt in db_titles_lower:
                    if t and dt and len(min(t, dt, key=len)) > 5 and (t in dt or dt in t):
                        found = True
                        break
        if found:
            in_db.append(fname)
        else:
            not_in_db.append(fname)
    return in_db, not_in_db


def run_research_books(in_db_selected: list[str], not_in_db_selected: list[str]):
    """Run agent_librarian_assistant for each selected book, one by one, streaming output."""
    selected = in_db_selected + not_in_db_selected
    if not selected:
        yield "⚠️  No books selected."
        return

    total = len(selected)
    statuses = [f"[{i + 1}/{total}] ⏳ Pending: {b}" for i, b in enumerate(selected)]

    _PRE_STYLE = (
        'font-family: ui-monospace, Menlo, \'Cascadia Code\', monospace;'
        'font-size: 0.82rem; white-space: pre; overflow-x: auto;'
        'margin: 0; padding: 0.75em; line-height: 1.45;'
    )

    def _render(live_lines: list[str]) -> str:
        header = _html.escape("\n".join(statuses))
        log = ""
        if live_lines:
            tail = "\n".join(live_lines[-100:])
            log = "\n" + "─" * 60 + "\n" + _html.escape(tail)
        return f'<div style="overflow-x:auto"><pre style="{_PRE_STYLE}">{header}{log}</pre></div>'

    yield _render([])

    env = {**os.environ, "NO_COLOR": "1", "PYTHONUNBUFFERED": "1", "TERM": "dumb", "FORCE_COLOR": "0", "COLUMNS": "200"}
    for i, book in enumerate(selected):
        statuses[i] = f"[{i + 1}/{total}] 🔬 Running: {book}"
        live_lines: list[str] = []
        yield _render(live_lines)

        path = str(SOURCES_DIR / book)
        proc = subprocess.Popen(
            [sys.executable, str(LIBRARIAN_ASSISTANT_APP), "--path", path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(PROJECT_ROOT),
            env=env,
            bufsize=1,
        )
        for raw_line in proc.stdout:
            stripped = _strip_ansi(raw_line.rstrip())
            if stripped:
                live_lines.append(stripped)
            yield _render(live_lines)
        proc.wait()

        if proc.returncode == 0:
            statuses[i] = f"[{i + 1}/{total}] ✅ Done: {book}"
        else:
            last_err = next((l for l in reversed(live_lines) if l.strip()), "unknown error")
            statuses[i] = f"[{i + 1}/{total}] ❌ Failed: {book}\n    {last_err}"
        yield _render([])

    _PRE_STYLE_FINAL = (
        'font-family: ui-monospace, Menlo, \'Cascadia Code\', monospace;'
        'font-size: 0.82rem; white-space: pre; overflow-x: auto;'
        'margin: 0; padding: 0.75em;'
    )
    done = sum(1 for s in statuses if "✅" in s)
    icon = "✅" if done == total else "⚠️"
    summary_line = f"\n{icon}  Finished: {done}/{total} book(s) successfully researched."
    final = _html.escape("\n".join(statuses) + summary_line)
    yield f'<div style="overflow-x:auto"><pre style="{_PRE_STYLE_FINAL}">{final}</pre></div>'


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------
# Books DB explorer helpers
# ---------------------------------------------------------------------------

sys.path.insert(0, str(Path(__file__).parent.parent))

from utils.sqlite_db.books_storage import DB_PATH  # noqa: E402


def _db_load_books() -> list[dict]:
    """Return all books from the DB ordered alphabetically by title."""
    import sqlite3

    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT id, title, author, publisher, year, edition, pages"
            " FROM books ORDER BY title"
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "id": r["id"],
            "Title": r["title"] or "",
            "Author": r["author"] or "",
            "Publisher": r["publisher"] or "",
            "Year": r["year"] or "",
            "Edition": r["edition"] or "",
            "Pages": r["pages"] or "",
        }
        for r in rows
    ]


def _db_load_chapters(book_id: int) -> list[dict]:
    """Return chapter rows for the given book id."""
    import sqlite3

    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT chapter_number, title, description"
            " FROM chapters WHERE book_id = ? ORDER BY chapter_number",
            (book_id,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "Ch.": r["chapter_number"] if r["chapter_number"] is not None else "",
            "Title": r["title"] or "",
            "Description": r["description"] or "",
        }
        for r in rows
    ]


def _db_load_urls(book_id: int) -> list[dict]:
    """Return URL rows for the given book id."""
    import sqlite3

    if not DB_PATH.exists():
        return []

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT category, url, label, author_name"
            " FROM book_urls WHERE book_id = ? ORDER BY category",
            (book_id,),
        ).fetchall()
    finally:
        conn.close()

    return [
        {
            "Category": r["category"] or "",
            "URL": r["url"] or "",
            "Label": r["label"] or "",
        }
        for r in rows
    ]


def _rows_to_dataframe_data(rows: list[dict]) -> tuple[list[list], list[str]]:
    """Convert list-of-dicts to (values, headers) suitable for gr.Dataframe."""
    if not rows:
        return [], []
    headers = list(rows[0].keys())
    values = [[r[h] for h in headers] for r in rows]
    return values, headers


# ---------------------------------------------------------------------------

_initial_volume_books = asyncio.run(_list_volume_files())
try:
    _initial_local_books = sorted(
        f.name for f in SOURCES_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
except Exception:
    _initial_local_books = []

try:
    _initial_in_db, _initial_not_in_db = _split_by_db_status(_initial_volume_books)
except Exception:
    _initial_in_db, _initial_not_in_db = [], list(_initial_volume_books)

BOOKS_CSS = """
.books-list .wrap { display: flex !important; flex-direction: column !important; gap: 4px !important; }
.books-list .wrap label { width: 100% !important; }
.books-list-notindb .wrap label { color: #e03535 !important; }
"""

with gr.Blocks(title="Research Assistant RAG") as demo:
    gr.Markdown(
        "# 📚 Research Assistant RAG\n"
        "Powered by Modal Volumes + Gemini FileSearch\n\n"
        "> No prior deployment needed — Modal functions run on demand via ephemeral containers."
    )

    # ------------------------------------------------------------------
    with gr.Tab("1 · Upload PDFs to Volume"):
        gr.Markdown(
            f"Push files from the local `data/sources/pdfs/` folder into the Modal Volume.  \n"
            "Run this whenever your source files change — no image rebuild required.  \n"
            "Leave all books unchecked to upload everything."
        )
        with gr.Row():
            refresh_local_btn = gr.Button("🔄  Refresh local file list", size="sm")
        upload_books_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_local_books),
            value=[],
            label="Select books to upload (leave empty = upload all)",
            elem_classes=["books-list"],
        )
        upload_btn = gr.Button("Upload to Volume", variant="primary")
        upload_status = gr.Textbox(label="Status", interactive=False, lines=8)
        upload_volume_books_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_volume_books),
            value=[],
            label="Volume contents after upload (also updates Ingest & Query lists)",
            visible=False,  # used as a hidden sink for the book-list refresh
        )

        refresh_local_btn.click(fn=refresh_local_books, outputs=upload_books_cb)
        upload_btn.click(
            fn=run_upload_pdfs,
            inputs=upload_books_cb,
            outputs=[upload_status, upload_volume_books_cb],
        )

    # ------------------------------------------------------------------
    with gr.Tab("2 · Ingest into Gemini Store"):
        gr.Markdown(
            "Index PDFs from the Modal Volume into the Gemini FileSearchStore.  \n"
            "Run this after uploading new PDFs.  \n"
            "Leave all books unchecked to ingest everything not yet in the store."
        )
        with gr.Row():
            refresh_ingest_btn = gr.Button("🔄  Refresh from Volume", size="sm")
            check_store_btn = gr.Button("🔍  Check store status", size="sm")
        ingested_books_cb = gr.CheckboxGroup(
            choices=[],
            value=[],
            label="Already ingested — click '🔍 Check store status' to populate",
            interactive=False,
            elem_classes=["books-list"],
        )
        ingest_books_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_volume_books),
            value=[],
            label="Select books to ingest (leave empty = ingest all)",
            elem_classes=["books-list"],
        )
        ingest_btn = gr.Button("Ingest PDFs", variant="primary")
        ingest_status = gr.Textbox(label="Status", interactive=False, lines=16, max_lines=60)

        refresh_ingest_btn.click(
            fn=refresh_ingest_books,
            outputs=ingest_books_cb,
        )
        check_store_btn.click(
            fn=refresh_ingest_status,
            outputs=[ingested_books_cb, ingest_books_cb],
        )
        ingest_btn.click(fn=run_ingest, inputs=ingest_books_cb, outputs=ingest_status,
                         show_progress="hidden")

    # ------------------------------------------------------------------
    with gr.Tab("3 · Books DB"):
        gr.Markdown(
            "Browse the local books database populated by the Librarian Assistant agent."
        )
        db_refresh_btn = gr.Button("🔄 Refresh", size="sm")

        _initial_books_rows, _initial_books_headers = _rows_to_dataframe_data(_db_load_books())
        books_df = gr.Dataframe(
            value=_initial_books_rows,
            headers=_initial_books_headers or ["id", "Title", "Author", "Publisher", "Year", "Edition", "Pages"],
            label=f"Books ({len(_initial_books_rows)} total)",
            interactive=False,
            wrap=True,
            column_widths=["4%", "30%", "22%", "14%", "6%", "6%", "6%"],
        )

        gr.Markdown("#### Chapters — click a row in the table above to load")
        chapters_df = gr.Dataframe(
            value=[],
            headers=["Ch.", "Title", "Description"],
            label="Chapters",
            interactive=False,
            wrap=True,
            column_widths=["5%", "25%", "70%"],
        )
        urls_df = gr.Dataframe(
            value=[],
            headers=["Category", "URL", "Label"],
            label="URLs",
            interactive=False,
            wrap=True,
        )

        # ── per-session state: ordered list of book ids ──
        book_ids_state = gr.State([r[0] for r in _initial_books_rows] if _initial_books_rows else [])

        def _db_refresh():
            rows = _db_load_books()
            data, headers = _rows_to_dataframe_data(rows)
            ids = [r[0] for r in data] if data else []
            return (
                gr.Dataframe(value=data, headers=headers or ["id", "Title", "Author", "Publisher", "Year", "Edition", "Pages"], label=f"Books ({len(data)} total)"),
                gr.Dataframe(value=[], headers=["Ch.", "Title", "Description"]),
                gr.Dataframe(value=[], headers=["Category", "URL", "Label"]),
                ids,
            )

        def _on_row_select(evt: gr.SelectData, current_ids: list[int]):
            row_idx = evt.index[0]
            if row_idx >= len(current_ids):
                return gr.Dataframe(value=[]), gr.Dataframe(value=[])
            book_id = current_ids[row_idx]
            ch_rows = _db_load_chapters(book_id)
            url_rows = _db_load_urls(book_id)
            ch_data, ch_headers = _rows_to_dataframe_data(ch_rows)
            url_data, url_headers = _rows_to_dataframe_data(url_rows)
            return (
                gr.Dataframe(value=ch_data, headers=ch_headers or ["Ch.", "Title", "Description"], label=f"Chapters ({len(ch_data)})"),
                gr.Dataframe(value=url_data, headers=url_headers or ["Category", "URL", "Label"], label=f"URLs ({len(url_data)})"),
            )

        db_refresh_btn.click(fn=_db_refresh, outputs=[books_df, chapters_df, urls_df, book_ids_state])
        books_df.select(fn=_on_row_select, inputs=book_ids_state, outputs=[chapters_df, urls_df])

    # ------------------------------------------------------------------
    with gr.Tab("4 · Research Books"):
        gr.Markdown(
            "Select books from the Modal Volume to research with the Librarian Assistant agent.  \n"
            "Books shown in **red** are not yet in the local database and are the primary candidates.  \n"
            "The agent researches each selected book one by one and saves metadata to `books.db`."
        )
        refresh_research_btn = gr.Button("🔄 Refresh list", size="sm")

        in_db_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_in_db),
            value=[],
            label=f"Already in database — {len(_initial_in_db)} book(s)",
            elem_classes=["books-list"],
        )
        not_in_db_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_not_in_db),
            value=[],
            label=f"Not in database — {len(_initial_not_in_db)} book(s)",
            elem_classes=["books-list", "books-list-notindb"],
        )
        research_btn = gr.Button("🔬 Research selected books", variant="primary")
        research_status = gr.HTML(value="")

        async def _refresh_research():
            vf = await _list_volume_files()
            try:
                idb, nidb = _split_by_db_status(vf)
            except Exception:
                idb, nidb = [], vf[:]
            return (
                gr.CheckboxGroup(choices=_enumerate_books(idb), value=[], label=f"Already in database — {len(idb)} book(s)", elem_classes=["books-list"]),
                gr.CheckboxGroup(choices=_enumerate_books(nidb), value=[], label=f"Not in database — {len(nidb)} book(s)", elem_classes=["books-list", "books-list-notindb"]),
            )

        refresh_research_btn.click(fn=_refresh_research, outputs=[in_db_cb, not_in_db_cb])
        research_btn.click(fn=run_research_books, inputs=[in_db_cb, not_in_db_cb], outputs=research_status, show_progress="hidden")

    # ------------------------------------------------------------------
    with gr.Tab("5 · Query"):
        gr.Markdown(
            "Ask a question about the ingested documents.  \n"
            "Select specific books to scope the answer, or leave all unchecked to search the full store."
        )
        with gr.Row():
            refresh_query_btn = gr.Button("🔄  Refresh book list from Volume", size="sm")
        query_books_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_volume_books),
            value=[],
            label="Scope to specific books (leave empty = search all)",
            elem_classes=["books-list"],
        )
        question_input = gr.Textbox(
            label="Question",
            placeholder="e.g. What is a martingale?",
            lines=2,
        )

        # Librarian integration
        use_librarian_cb = gr.Checkbox(
            label="Use Librarian Agent to suggest relevant books",
            value=False,
            info="Searches the local book database and auto-selects matching books in the list above.",
        )
        with gr.Group(visible=False) as librarian_group:
            suggest_btn = gr.Button("🔍  Suggest books from library database", variant="secondary")
            librarian_notes = gr.Markdown(
                label="Librarian suggestions",
                value="",
                min_height=80,
                height=400,
            )

        query_btn = gr.Button("Ask", variant="primary")
        answer_output = gr.Markdown(
            label="Answer",
            latex_delimiters=[
                {"left": "$$", "right": "$$", "display": True},
                {"left": "$",  "right": "$",  "display": False},
            ],
        )

        refresh_query_btn.click(
            fn=refresh_query_books,
            outputs=query_books_cb,
        )
        use_librarian_cb.change(
            fn=lambda checked: gr.Group(visible=checked),
            inputs=use_librarian_cb,
            outputs=librarian_group,
        )
        suggest_btn.click(
            fn=run_librarian_suggest,
            inputs=question_input,
            outputs=[query_books_cb, librarian_notes],
        )
        query_btn.click(
            fn=run_query,
            inputs=[question_input, query_books_cb],
            outputs=answer_output,
        )
        question_input.submit(
            fn=run_query,
            inputs=[question_input, query_books_cb],
            outputs=answer_output,
        )

    # ------------------------------------------------------------------
    with gr.Tab("6 · Purge Store"):
        gr.Markdown(
            "Remove individual files or wipe the entire Gemini FileSearchStore."
        )
        with gr.Row():
            check_purge_btn = gr.Button("🔍  Load store contents", size="sm")
        purge_files_cb = gr.CheckboxGroup(
            choices=[],
            value=[],
            label="Select files to remove — click '🔍 Load store contents' first",
            elem_classes=["books-list"],
        )
        remove_btn = gr.Button("🗑️  Remove selected files", variant="secondary")
        gr.Markdown(
            "---\n"
            "### ⚠️ Purge everything\n"
            "Deletes **all** files **and** the store itself — irreversible."
        )
        purge_btn = gr.Button("Purge entire FileSearchStore", variant="stop")
        purge_status = gr.Textbox(label="Status", interactive=False, lines=6)

        check_purge_btn.click(fn=refresh_purge_status, outputs=purge_files_cb)
        remove_btn.click(fn=run_remove_files, inputs=purge_files_cb, outputs=purge_status)
        purge_btn.click(fn=run_purge, outputs=[purge_status, purge_files_cb])


if __name__ == "__main__":
    demo.queue(default_concurrency_limit=5)
    demo.launch(
        theme=gr.themes.Default(),
        css=BOOKS_CSS,
    )
