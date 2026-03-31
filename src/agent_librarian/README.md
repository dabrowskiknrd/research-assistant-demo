# Librarian Agent

Accepts a topic query and returns a list of relevant books by searching book descriptions and chapter descriptions in the local SQLite database.

## Execution flow

```
User enters a topic query
│
└── answer_query(query)
    │
    ├── search_books(query)           ← searches book & chapter descriptions in SQLite
    │
    └── (retry with rephrased query if few/no results)
        └── LLM synthesises results into a recommendation list
```

The agent runs in execute-only mode — no planning phase is needed for a single-turn lookup.

## Tools

| Tool | Description |
|------|-------------|
| `search_books` | Full-text keyword search across book descriptions and chapter titles/descriptions in the local database. Returns matched books with relevant chapter excerpts. |

## Database

Reads from `data/database/books/books.db` at the project root. Tables used:

- **`books`** — title, author, publisher, year, edition, description
- **`chapters`** — chapter_number, title, description (FK → books)

## Usage

```bash
python src/agent_libraian/app.py
```

You will be prompted to enter a topic or question. Leave the input empty to quit.
