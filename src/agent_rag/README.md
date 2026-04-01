# Research Assistant RAG — Modal (Volumes variant)

## Architecture

- A persistent `modal.Volume` (`research-assistant-rag-pdfs`) stores source files.
  Files are uploaded once from the local `data/sources/pdfs/` folder via `upload_books`. A subset can be specified by passing `--books`.
- `ingest` reads files from the Volume and uploads them to a Gemini `FileSearchStore` (store name persisted in a `modal.Dict`).
  An optional `books` list restricts which files are ingested.
- `query` calls Gemini with the `FileSearch` tool. When a `books` list is supplied the prompt instructs the model to answer only from those sources.
- `web_query` exposes the same query as a FastAPI HTTP endpoint.

## Why Volumes vs. `add_local_dir`?

- `add_local_dir` bakes PDFs into the container image — every PDF change triggers a full image rebuild.
- Volumes decouple PDF storage from the image: update books by running `upload_books` without rebuilding the image at all.
- The Volume persists independently of deployments and is shareable across multiple apps or functions.

## Authentication & Secrets

### 1. Authenticate with Modal

Install the Modal client and log in (or create an account):

```bash
pip install modal
modal setup
```

`modal setup` opens a browser to complete OAuth. Your token is saved to `~/.modal.toml` and is reused for all subsequent CLI commands.

### 2. Set up your Gemini API key

Create a `.env` file in the project root:

```
GEMINI_API_KEY=<your-google-ai-studio-key>
```

Get your key from [Google AI Studio](https://aistudio.google.com/app/apikey).

> `modal.Secret.from_dotenv()` reads this file automatically at run time and injects it into the container — no manual secret creation needed.

### 3. (Optional) Named secret for `modal deploy`

If you deploy the app permanently with `modal deploy`, the container runs without your local machine. In that case, create a named secret in Modal once:

```bash
modal secret create gemini-api-key GEMINI_API_KEY=<your-google-ai-studio-key>
```

Then switch the secret in `app.py` from `modal.Secret.from_dotenv(__file__)` to `modal.Secret.from_name("gemini-api-key")`.

---

## Quick-start

**1.** Add PDF files to `data/sources/pdfs/`

**2.** Authenticate with Modal and add your Gemini key to `.env` (see [Authentication & Secrets](#authentication--secrets) above).

**3.** Upload books to the Modal Volume (only needed once, or when books change):
```bash
modal run src/agent_rag/app.py --action upload_books
```

Upload only a specific subset:
```bash
modal run src/agent_rag/app.py --action upload_books \
    --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf|Analysis I 4th ED - Terence Tao, Springer, 2022.pdf"
```

**4.** Ingest books into Gemini FileSearchStore (only needed once, or after upload):
```bash
modal run src/agent_rag/app.py --action ingest
```

Ingest only a specific subset:
```bash
modal run src/agent_rag/app.py --action ingest \
    --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf"
```

**5.** Query with a specific set of books:
```bash
modal run src/agent_rag/app.py --action query \
    --question "What is a martingale?" \
    --books "Probability Theory 3rd ED - Achim Klenke, Springer, 2020.pdf|Probability and Stochastics 1st ED - Erhan Çinlar, Springer, 2011.pdf"
```

**6.** (Optional) Deploy and serve the HTTP query endpoint:
```bash
modal deploy src/agent_rag/app.py
```

Then POST to the printed URL:
```bash
curl -X POST <url>/query -H "Content-Type: application/json" \
     -d '{"question": "What is a sigma-algebra?", "books": ["Measure Theory, Probability, and Stochastic Processes - Jean-François Le Gall, Springer, 2022.pdf"]}'
```

## Actions reference

| Action | Description |
|---|---|
| `upload_books` | Push local `data/sources/pdfs/` files into the Modal Volume. Re-run whenever source files change. |
| `ingest` | Upload files from the Volume to the Gemini FileSearchStore. Re-run after `upload_books`. |
| `query` | Ask a question, optionally restricted to a subset of books. |
| `purge_store` | Delete all files and the store from Gemini (data-retention control). Opt-in only. |

## HTTP API

`POST /query`

```json
{
  "question": "Explain the central limit theorem.",
  "books": ["All of Statistics - Larry Wasserman, Springer, 2004.pdf"]
}
```

Response:
```json
{
  "question": "Explain the central limit theorem.",
  "books": ["All of Statistics - Larry Wasserman, Springer, 2004.pdf"],
  "answer": "..."
}
```

`books` is optional — omit it to query against the full store.
