"""
Research Assistant RAG — Modal (Volumes variant)
=================================================

Architecture
------------
• A persistent modal.Volume ("research-assistant-rag-pdfs") stores source files.
  Files are uploaded once from the local data/sources/pdfs/ folder via
  ``upload_books``.  A subset can be specified by passing ``--books``.
• ``ingest`` reads files from the Volume and uploads them to a Gemini
  FileSearchStore (store name persisted in a modal.Dict).
  An optional ``books`` list restricts which files are ingested.
• ``query`` calls Gemini with the FileSearch tool.  When a ``books`` list is
  supplied the prompt instructs the model to answer only from those sources.
• ``web_query`` exposes the same query as a FastAPI HTTP endpoint.

Why Volumes vs. add_local_dir?
-------------------------------
• add_local_dir bakes PDFs into the container image — every PDF change
  triggers a full image rebuild.
• Volumes decouple PDF storage from the image: update books by running
  ``upload_books`` without rebuilding the image at all.
• The Volume persists independently of deployments and is shareable across
  multiple apps or functions.

Quick-start
-----------
1. Add PDF files to data/sources/pdfs/

2. Create a .env file in the project root:
      GEMINI_API_KEY=<your-key>

3. Upload books to the Modal Volume (only needed once, or when books change):
      modal run src/agent_rag/app.py --action upload_books

   Upload only a specific subset:
      modal run src/agent_rag/app.py --action upload_books \\
          --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf|Analysis I 4th ED - Terence Tao, Springer, 2022.pdf"

4. Ingest books into Gemini FileSearchStore (only needed once, or after upload):
      modal run src/agent_rag/app.py --action ingest

   Ingest only a specific subset:
      modal run src/agent_rag/app.py --action ingest \\
          --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf"

5. Query with a specific set of books:
      modal run src/agent_rag/app.py --action query \\
          --question "What is a martingale?" \\
          --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf,Probability and Stochastics 1st ED - Erhan Çinlar, Springer, 2011.pdf"

6. (Optional) Deploy and serve the HTTP query endpoint:
      modal deploy src/agent_rag/app.py
   Then POST to the printed URL:
      curl -X POST <url>/query -H "Content-Type: application/json" \\
           -d '{"question": "What is a sigma-algebra?", "books": ["Measure Theory, Probability, and Stochastic Processes - Jean-François Le Gall, Springer, 2022.pdf"]}'
"""

import os
import time
from pathlib import Path

import modal
from fastapi import FastAPI
from pydantic import BaseModel

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

GEMINI_MODEL = "gemini-3.1-flash-lite-preview"
STORE_DICT_NAME = "research-assistant-rag-store"
STORE_KEY = "file_search_store_name"
SUPPORTED_EXTENSIONS = (".pdf", ".md")

# Local path — only used by the upload_books local_entrypoint action
SOURCES_DIR = Path(__file__).parent.parent.parent / "data" / "sources" / "pdfs"

# Mount path inside Modal containers
VOLUME_PDF_PATH = "/pdfs"

# ---------------------------------------------------------------------------
# Modal Volume — stores source files persistently, independent of the image
# ---------------------------------------------------------------------------

pdf_volume = modal.Volume.from_name("research-assistant-rag-pdfs", create_if_missing=True)

# ---------------------------------------------------------------------------
# Modal image — no PDFs baked in; only runtime dependencies
# ---------------------------------------------------------------------------

image = (
    modal.Image.debian_slim(python_version="3.12")
    .pip_install("google-genai>=1.68.0", "fastapi[standard]>=0.135.2")
)

# ---------------------------------------------------------------------------
# Modal app
# ---------------------------------------------------------------------------

app = modal.App("research-assistant-rag", image=image)

# Persistent key-value store for the FileSearchStore name
store_dict = modal.Dict.from_name(STORE_DICT_NAME, create_if_missing=True)

# ---------------------------------------------------------------------------
# Helpers (run inside the Modal container)
# ---------------------------------------------------------------------------


def _get_or_create_store(client) -> str:
    """Return the name of the FileSearchStore, creating it if necessary."""
    existing = store_dict.get(STORE_KEY, None)
    if existing:
        print(f"[store] Reusing existing FileSearchStore: {existing}")
        return existing

    store = client.file_search_stores.create(
        config={"display_name": "research-assistant-rag-store"}
    )
    store_dict[STORE_KEY] = store.name
    print(f"[store] Created new FileSearchStore: {store.name}")
    return store.name


def _poll(client, operation):
    """Block until a long-running Gemini operation completes."""
    while not operation.done:
        time.sleep(5)
        operation = client.operations.get(operation)
    return operation


def _filter_books(all_files: list[Path], books: list[str] | None) -> list[Path]:
    """Return only the files whose names appear in ``books`` (case-insensitive).

    If ``books`` is *None* or empty all files are returned unchanged.
    """
    if not books:
        return all_files
    lower_books = {b.lower() for b in books}
    return [f for f in all_files if f.name.lower() in lower_books]


def _build_rag_prompt(question: str, books: list[str] | None) -> str:
    """Build the prompt that constrains the model to the requested book subset."""
    if books:
        book_lines = "\n".join(f"  - {b}" for b in books)
        scope = (
            "You have access to a document store.\n"
            "Answer the question using ONLY the following books:\n"
            f"{book_lines}\n\n"
            "Do not draw on any other documents that may be present in the store."
        )
    else:
        scope = (
            "You have access to a document store. "
            "Answer the question using all available documents."
        )
    return f"{scope}\n\nQuestion: {question}"


# ---------------------------------------------------------------------------
# Ingest: read files from the Volume and upload to the Gemini FileSearchStore
# ---------------------------------------------------------------------------


@app.function(
    secrets=[modal.Secret.from_dotenv(__file__)],
    volumes={VOLUME_PDF_PATH: pdf_volume},
    timeout=600,
)
def ingest(books: list[str] | None = None) -> dict:
    """
    Upload PDF/Markdown files from the Modal Volume to the Gemini FileSearchStore.

    Parameters
    ----------
    books:
        Optional list of filenames (e.g. ``["BookTitle.pdf"]``) to ingest.
        When *None* (default) every supported file in the Volume is ingested.
        Run ``upload_books`` first to populate the Volume.
    """
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    store_name = _get_or_create_store(client)

    all_files = sorted(
        f for f in Path(VOLUME_PDF_PATH).iterdir()
        if f.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    doc_files = _filter_books(all_files, books)

    if not doc_files:
        print(
            "[ingest] No matching files found in Volume at /pdfs — "
            "run upload_books first."
        )
        return {"store_name": store_name, "uploaded": 0}

    print(f"[ingest] Found {len(doc_files)} file(s): {[f.name for f in doc_files]}")

    uploaded = 0
    for pdf_path in doc_files:
        print(f"[ingest]   Uploading {pdf_path.name} …")
        operation = client.file_search_stores.upload_to_file_search_store(
            file=str(pdf_path),
            file_search_store_name=store_name,
            config={"display_name": pdf_path.name},
        )
        _poll(client, operation)
        print(f"[ingest]   ✓  {pdf_path.name} indexed.")
        uploaded += 1

    print(f"[ingest] Done. {uploaded} file(s) uploaded → store: {store_name}")
    return {"store_name": store_name, "uploaded": uploaded}


# ---------------------------------------------------------------------------
# Query: ask a question, restricted to the specified book subset
# ---------------------------------------------------------------------------


@app.function(
    secrets=[modal.Secret.from_dotenv(__file__)],
)
def query(question: str, books: list[str] | None = None) -> str:
    """Ask a question against the indexed documents.

    Parameters
    ----------
    question:
        The research question to answer.
    books:
        Optional list of filenames (e.g. ``["BookTitle.pdf"]``) that scope
        the answer.  The model is instructed to use *only* those sources.
        When *None* the full store is used.
    """
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    store_name = store_dict.get(STORE_KEY, None)
    if not store_name:
        return (
            "No FileSearchStore found. "
            "Run `modal run src/agent_rag/app.py --action ingest` first."
        )

    prompt = _build_rag_prompt(question=question, books=books)

    print(f"[query] Store   : {store_name}")
    print(f"[query] Books   : {books or 'all'}")
    print(f"[query] Question: {question}")

    response = client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[
                types.Tool(
                    file_search=types.FileSearch(
                        file_search_store_names=[store_name]
                    )
                )
            ]
        ),
    )

    return response.text


# ---------------------------------------------------------------------------
# Purge: delete all files + the store from Gemini (data-retention control)
# ---------------------------------------------------------------------------


@app.function(
    secrets=[modal.Secret.from_dotenv(__file__)],
)
def purge_store() -> dict:
    """
    Remove every file from the Gemini FileSearchStore and then delete the store
    itself, ensuring no content is retained on Google's servers.

    The persistent Modal Dict entry is also cleared so the next ``ingest`` run
    creates a brand-new store.

    This action is NOT called automatically — invoke it explicitly:
        modal run src/agent_rag/app.py --action purge_store
    """
    from google import genai

    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

    store_name = store_dict.get(STORE_KEY, None)
    if not store_name:
        print("[purge] No FileSearchStore found in Modal Dict — nothing to purge.")
        return {"purged_files": 0, "store_deleted": False}

    print(f"[purge] Purging FileSearchStore: {store_name}")

    removed = 0
    for file_entry in client.file_search_stores.list_files(
        file_search_store_name=store_name
    ):
        client.file_search_stores.remove_file(
            file_search_store_name=store_name,
            file_search_store_file_id=file_entry.name,
        )
        print(f"[purge]   Removed: {file_entry.name}")
        removed += 1

    client.file_search_stores.delete(name=store_name)
    print(f"[purge] Deleted FileSearchStore: {store_name}")

    del store_dict[STORE_KEY]
    print("[purge] Cleared store reference from Modal Dict.")

    return {"purged_files": removed, "store_deleted": True}


# ---------------------------------------------------------------------------
# Optional HTTP endpoint (modal deploy + modal serve)
# ---------------------------------------------------------------------------


@app.function(
    secrets=[modal.Secret.from_dotenv(__file__)],
)
@modal.asgi_app()
def web_query():
    web_app = FastAPI(title="Research Assistant RAG API")

    class QueryRequest(BaseModel):
        question: str
        books: list[str] | None = None

    @web_app.post("/query")
    async def http_query(req: QueryRequest):
        answer = query.remote(req.question, req.books)
        return {"question": req.question, "books": req.books, "answer": answer}

    return web_app


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------


@app.local_entrypoint()
def main(
    action: str = "query",
    question: str = "Summarize the documents.",
    books: str = "",  # pipe-separated list of filenames (| delimiter, since filenames contain commas)
):
    """
    Actions
    -------
    upload_books  Push local data/sources/pdfs/ files into the Modal Volume.
                  Run this whenever your source files change.
    ingest        Upload files from the Volume to the Gemini FileSearchStore.
                  Run this after upload_books (or when you want to re-index).
    query         Ask a question, optionally restricted to a subset of books.
    purge_store   Delete all files and the store from Gemini (data-retention
                  control). Not called automatically — opt-in only.

    Examples
    --------
    modal run src/agent_rag/app.py --action upload_books
    modal run src/agent_rag/app.py --action ingest
    modal run src/agent_rag/app.py --action query \\
        --question "What is a martingale?" \\
        --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf|Probability and Stochastics 1st ED - Erhan Çinlar, Springer, 2011.pdf"
    modal run src/agent_rag/app.py --action purge_store
    """
    book_list: list[str] | None = (
        [b.strip() for b in books.split("|") if b.strip()] if books else None
    )

    if action == "upload_books":
        if not SOURCES_DIR.exists():
            print(f"Sources directory not found: {SOURCES_DIR}")
            return

        doc_files = sorted(
            f for f in SOURCES_DIR.iterdir()
            if f.suffix.lower() in SUPPORTED_EXTENSIONS
        )
        doc_files = _filter_books(doc_files, book_list)

        if not doc_files:
            print(f"No matching files found in {SOURCES_DIR}")
            return

        print(f"Uploading {len(doc_files)} file(s) to Modal Volume …")
        with pdf_volume.batch_upload(force=True) as batch:
            for pdf_path in doc_files:
                batch.put_file(str(pdf_path), f"/{pdf_path.name}")
                print(f"  queued {pdf_path.name}")

        print("Upload complete. Volume contents:")
        for entry in pdf_volume.listdir("/"):
            print(f"  {entry.path}")

    elif action == "ingest":
        result = ingest.remote(book_list)
        print(f"\n[done] {result}")

    elif action == "query":
        answer = query.remote(question, book_list)
        print(f"\n[answer]\n{answer}")

    elif action == "purge_store":
        result = purge_store.remote()
        print(f"\n[done] {result}")

    else:
        print(
            f"Unknown action '{action}'. "
            "Choose: upload_books | ingest | query | purge_store"
        )
