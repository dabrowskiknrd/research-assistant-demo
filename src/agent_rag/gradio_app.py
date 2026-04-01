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


# ---------------------------------------------------------------------------
# Helper: list files in the Modal Volume
# ---------------------------------------------------------------------------

def _list_volume_files() -> list[str]:
    """Return the sorted list of filenames currently in the PDF Volume."""
    try:
        return sorted(entry.path.lstrip("/") for entry in pdf_volume.listdir("/"))
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


def run_upload_pdfs(selected_books: list[str]):
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
            ), _list_volume_files()

        if selected_books:
            lower = {b.lower() for b in selected_books}
            doc_files = [f for f in all_files if f.name.lower() in lower]
        else:
            doc_files = all_files

        if not doc_files:
            return "⚠️  None of the selected files exist locally.", _list_volume_files()

        names = []
        with pdf_volume.batch_upload(force=True) as batch:
            for pdf_path in doc_files:
                batch.put_file(str(pdf_path), f"/{pdf_path.name}")
                names.append(pdf_path.name)

        status = (
            f"✅  Uploaded {len(names)} file(s) to Modal Volume:\n"
            + "\n".join(f"  • {n}" for n in names)
        )
        return status, _list_volume_files()
    except Exception as exc:
        return f"❌  Error: {exc}", _list_volume_files()


def run_ingest(selected_books: list[str]):
    """Invoke ``modal run app.py --action ingest`` as a subprocess."""
    args = ["--action", "ingest"]
    if selected_books:
        args += ["--books", "|".join(selected_books)]
    out, success = _run_modal_action(*args)
    if not success:
        return f"❌  Error:\n{out}"
    return f"✅  {out}"


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
        return f"❌  Error:\n{out}"
    return f"✅  {out}"


def refresh_volume_books():
    """Return the current book list from the Volume for CheckboxGroup updates."""
    books = _list_volume_files()
    return (
        gr.CheckboxGroup(choices=books, value=[]),
        gr.CheckboxGroup(choices=books, value=[]),
    )


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
# UI
# ---------------------------------------------------------------------------

_initial_volume_books = _list_volume_files()
try:
    _initial_local_books = sorted(
        f.name for f in SOURCES_DIR.iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
except Exception:
    _initial_local_books = []

BOOKS_CSS = """
.books-list .wrap { display: flex !important; flex-direction: column !important; gap: 4px !important; }
.books-list .wrap label { width: 100% !important; }
"""

with gr.Blocks(title="Research Assistant RAG", css=BOOKS_CSS) as demo:
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
            "Leave all books unchecked to ingest everything in the Volume."
        )
        with gr.Row():
            refresh_ingest_btn = gr.Button("🔄  Refresh book list from Volume", size="sm")
        ingest_books_cb = gr.CheckboxGroup(
            choices=_enumerate_books(_initial_volume_books),
            value=[],
            label="Select books to ingest (leave empty = ingest all)",
            elem_classes=["books-list"],
        )
        ingest_btn = gr.Button("Ingest PDFs", variant="primary")
        ingest_status = gr.Textbox(label="Status", interactive=False, lines=4)

        refresh_ingest_btn.click(
            fn=lambda: gr.CheckboxGroup(choices=_enumerate_books(_list_volume_files()), value=[], elem_classes=["books-list"]),
            outputs=ingest_books_cb,
        )
        ingest_btn.click(fn=run_ingest, inputs=ingest_books_cb, outputs=ingest_status)

    # ------------------------------------------------------------------
    with gr.Tab("3 · Query"):
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
        query_btn = gr.Button("Ask", variant="primary")
        answer_output = gr.Markdown(
            label="Answer",
            latex_delimiters=[
                {"left": "$$", "right": "$$", "display": True},
                {"left": "$",  "right": "$",  "display": False},
            ],
        )

        refresh_query_btn.click(
            fn=lambda: gr.CheckboxGroup(choices=_enumerate_books(_list_volume_files()), value=[], elem_classes=["books-list"]),
            outputs=query_books_cb,
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
    with gr.Tab("4 · Purge Store"):
        gr.Markdown(
            "## ⚠️ Danger Zone\n"
            "Removes **all files** from the Gemini FileSearchStore and deletes the store itself.  \n"
            "This ensures no content is retained on Google's servers.  \n\n"
            "After purging, run **Ingest** again to rebuild the store from the Volume."
        )
        purge_btn = gr.Button("Purge FileSearchStore", variant="stop")
        purge_status = gr.Textbox(label="Status", interactive=False, lines=4)
        purge_btn.click(fn=run_purge, outputs=purge_status)


if __name__ == "__main__":
    demo.launch()
