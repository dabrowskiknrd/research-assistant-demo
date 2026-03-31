# Paths-based Librarian Assistant Agent

Accepts a file path that follows the project naming convention, parses all book metadata directly from the filename, then runs a full plan → execute → save research cycle.

## Execution flow

```
User enters a file path
│
└── parse_path_input(raw)
    │  Extracts title, edition, author(s), publisher, year, file extension
    │
    └── research_source(parsed)
        │
        ├── [PLAN phase — LLM model]
        │   └── generate_plan(todos)          ← builds ordered research plan
        │
        └── [EXECUTE phase — LLM model]
            │
            ├── delegate_search([...])         ← parallel subagents: editions, author names
            ├── delegate_search([...])         ← parallel subagents: DOI, ISBN, pages
            ├── delegate_search([...])         ← parallel subagents: URLs (publisher, GitHub, author sites)
            ├── delegate_search([ch1, ch2, …]) ← all chapter pages fetched in parallel
            │   └── search subagents run on LLM model (max 10 concurrent)
            ├── save_book(...)                 ← persists book record to SQLite
            ├── save_chapter(...) × N          ← one call per chapter (after delegate_search)
            └── save_book_path(...)            ← links the original file path to the book record
```

Sequential todos guarantee that metadata is fully resolved before chapters are explored.
Within each `delegate_search` call, subagents run concurrently (up to 10 by a semaphore).

## Input format

Paths must follow the project naming convention:

```
<Title [optional edition]> - <Author(s)>, <Publisher>, <Year>.<ext>
```

Examples:

```
data/sources/pdfs/All of Statistics - Larry Wasserman, Springer, 2004.pdf
Analysis I 4th ED - Terence Tao, Springer, 2022.md
Introduction to Algorithms 3rd ED - Cormen, Leiserson, Rivest, Stein, MIT Press, 2009.pdf
```

The parser extracts:

| Field | Example |
|-------|---------|
| `title` | `All of Statistics` |
| `edition_str` | `4th ED` (empty when absent) |
| `author` | `Terence Tao` |
| `publisher` | `Springer` |
| `year` | `2022` |
| `suffix` | `.pdf` |

## Tools

| Tool | Description |
|------|-------------|
| `generate_plan` | Creates an ordered list of research todos (plan phase). |
| `modify_todo` | Removes a completed todo during execution. |
| `delegate_search` | Spawns parallel search subagents (up to 20 queries per call, 10 concurrent). |
| `search_web` | Web search used by subagents (Exa). |
| `fetch_url` | Fetches a full page — used by subagents to retrieve publisher chapter pages. |
| `save_book` | Saves book metadata, URLs, and optional inline chapters to SQLite. |
| `save_chapter` | Saves a single chapter (number, title, description, URL) for an already-saved book. |
| `save_book_path` | Records the mapping from the original file path to the saved book record. |

## Research plan

The plan phase generates a tailored todo list that always includes, in order:

1. Resolve full author name(s) if any name looks like a surname only
2. Find all editions (to identify which edition matches the provided year)
3. Confirm edition number, pages, DOI, eBook ISBN
4. Find publisher book page URL
5. Find all relevant URLs (companion website, GitHub repository, author website(s))
6. Research book description and chapter list
7. Explore individual chapter links for chapter-level abstracts
8. Save book record to the database
9. Save path mapping to the database

## Database

Reads/writes `data/database/books/books.db` at the project root. Tables written:

- **`books`** — title, subtitle, author, publisher, year, edition, pages, doi, isbn_ebook, description, urls_json
- **`chapters`** — chapter_number, title, description, url (FK → books)
- **`book_paths`** — path, file_type (FK → books via title + author)

The `book_paths` table is updated by `save_book_path` as the final step, linking the original filename to its database record.

## Models

| Role | Model |
|------|-------|
| Orchestrator (plan + execute) | LLM model |
| Search subagents | LLM model |

## Environment variables

| Variable | Required | Description |
|----------|----------|-------------|
| `GOOGLE_API_KEY` | Yes | Google Generative AI API key (used by the `google-genai` client). |
| `EXA_API_KEY` | Yes | Exa API key for web search. |

## Usage

```bash
python src/agent_lib_paths_assistant/app.py
```

You will be prompted to enter a file path. Repeat for multiple sources. Leave the input empty to quit.
