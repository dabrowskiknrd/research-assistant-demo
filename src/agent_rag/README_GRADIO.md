# Research Assistant RAG — Gradio UI

A local web interface for the Modal RAG app. All heavy work (ingest, query, purge) runs in ephemeral Modal containers — no `modal deploy` needed.

## Prerequisites

- Modal credentials configured: `modal setup`
- `.env` file in the project root containing `GEMINI_API_KEY=<your-key>`
- PDFs placed in `data/sources/pdfs/`

## Running

```bash
python src/agent_rag/gradio_app.py
```

Opens at `http://localhost:7860`.

---

## Tabs

### 1 · Upload PDFs to Volume

Pushes files from your **local** `data/sources/pdfs/` folder into the persistent Modal Volume.

- Runs entirely locally via the Modal SDK — no container is spun up.
- Use the checkbox list to upload a subset, or leave all unchecked to upload everything.
- Re-run whenever your source files change. Volumes decouple storage from the image — no image rebuild is needed.

**Book list shown here**: files currently present on your **local machine** (`data/sources/pdfs/`).  
Click **🔄 Refresh local file list** to rescan the folder.

---

### 2 · Ingest into Gemini Store

Indexes files from the Modal Volume into the Gemini `FileSearchStore` so they can be searched.

- Runs as an ephemeral Modal container via `modal run`.
- Use the checkbox list to ingest a subset, or leave all unchecked to ingest everything in the Volume.
- Re-run after uploading new PDFs, or to re-index existing ones.

**Book list shown here**: files currently stored in the **Modal Volume** (i.e. what has been uploaded via Tab 1).  
Click **🔄 Refresh book list from Volume** to fetch the latest Volume contents.

> ⚠️ This list reflects the Volume, not the Gemini store. A file shown here has been uploaded but may not yet be ingested — run Ingest to index it.

---

### 3 · Query

Asks a question against the indexed documents using Gemini FileSearch.

- Runs as an ephemeral Modal container via `modal run`.
- **Select specific books** in the checkbox list to scope the answer to only those sources. Leave all unchecked to search across the full store.
- Answers are rendered as Markdown with LaTeX support (`$...$` and `$$...$$`).
- Press **Ask** or hit **Enter** in the question box to submit.

**Book list shown here**: files currently stored in the **Modal Volume**.  
Click **🔄 Refresh book list from Volume** to update the list.

---

### 4 · Purge Store

Deletes all files from the Gemini `FileSearchStore` and removes the store itself.

- Runs as an ephemeral Modal container via `modal run`.
- Use this for data-retention control — ensures no content is retained on Google's servers.
- After purging, run **Ingest** again to rebuild the store from the Volume.
- The Modal Volume is **not** affected — your PDFs remain in cloud storage.

> ⚠️ This is irreversible. The store and all its indexed content are permanently deleted.

---

## Understanding the Book Lists

There are three separate book lists across the tabs. They come from different sources:

| Tab | Book list source | What it shows |
|---|---|---|
| 1 · Upload | Local filesystem (`data/sources/pdfs/`) | Files on your machine, ready to upload |
| 2 · Ingest | Modal Volume (`research-assistant-rag-pdfs`) | Files already uploaded to cloud storage |
| 3 · Query | Modal Volume (`research-assistant-rag-pdfs`) | Files already uploaded to cloud storage |

The **Upload tab** and the **Ingest/Query tabs** can show different files if:
- You added new PDFs locally but haven't uploaded them yet (Upload tab shows them; Ingest/Query don't).
- You deleted local files after uploading (Upload tab no longer shows them; Ingest/Query still do).

Each list has a **🔄 Refresh** button that re-reads the source on demand. The lists are also populated once at startup.

---

## Typical Workflow

```
1. Add PDFs → data/sources/pdfs/
2. Tab 1: Upload PDFs to Volume      (runs locally)
3. Tab 2: Ingest into Gemini Store   (runs remotely, ~minutes per book)
4. Tab 3: Query                      (runs remotely, ~seconds)
```

Steps 2–3 only need to be repeated when your source files change.

---

## How Modal Functions Are Invoked

The Gradio handlers for Ingest, Query, and Purge run `modal run src/agent_rag/app.py --action <action>` as subprocesses. This is identical to running those commands in the terminal — Modal spins up ephemeral containers on demand with no persistent deployment required.

Upload uses the Modal Volume SDK directly from your local Python process; no container is involved.

---

## Relation to CLI

Every action in the UI has a CLI equivalent:

| UI action | CLI equivalent |
|---|---|
| Upload (all) | `modal run src/agent_rag/app.py --action upload_books` |
| Upload (subset) | `modal run src/agent_rag/app.py --action upload_books --books "A.pdf\|B.pdf"` |
| Ingest (all) | `modal run src/agent_rag/app.py --action ingest` |
| Ingest (subset) | `modal run src/agent_rag/app.py --action ingest --books "A.pdf"` |
| Query (all books) | `modal run src/agent_rag/app.py --action query --question "..."` |
| Query (subset) | `modal run src/agent_rag/app.py --action query --question "..." --books "A.pdf\|B.pdf"` |
| Purge | `modal run src/agent_rag/app.py --action purge_store` |

See [README.md](README.md) for the full CLI reference.
