# Research Assistant Demo

An agentic research assistant that processes PDFs, searches the web, and manages a local book database.

It is inspired by [build-your-own-deep-research-agent](https://github.com/hugobowne/build-your-own-deep-research-agent) by [Hugo Bowne-Anderson](https://github.com/hugobowne) and [Ivan Leo](https://github.com/ivanleomk), and reuses many ideas from that source.

## Setup

Requires [uv](https://docs.astral.sh/uv/) for dependency management.

```bash
# Install dependencies and install the project in editable mode
uv sync

# Install with dev dependencies
uv sync --group dev

# Run Modal Web authentication
modal setup

# Run Logfire Web authentication
uv run logfire auth
```

> **Note:** `uv sync` installs all dependencies and registers `src/` as the package
> root in editable mode. Modules such as `utils`, `agent_generic`,
> `agent_librarian`, and `agent_librarian_assistant` are then importable from any
> notebook or script using the project's venv — no `sys.path` manipulation needed.


## Usage

```bash
uv run research-assistant
```

## Development

```bash
# Lint
uv run ruff check .

# Format
uv run ruff format .

# Type check
uv run mypy src/
```

## Project Structure

```
.
├── src/
│   ├── agent_generic/          # Core Agent class, RunConfig, RunState, AgentContext
│   ├── agent_librarian/        # Librarian agent (manual input, plan → execute → save)
│   ├── agent_librarian_assistant/  # Paths-based agent (parses filename, plan → execute → save)
│   ├── research_assistant/     # Package entry point (uv run research-assistant)
│   ├── tools/                  # Tool definitions (web search, DB save/search, filesystem, …)
│   └── utils/                  # Shared utilities (SQLite DB, PDF/Mathpix conversion, …)
├── data/
│   ├── database/books/         # SQLite database (books.db)
│   └── sources/                # Source PDFs and converted files
├── notebooks/                  # Jupyter notebooks for analysis and exploration
├── pyproject.toml
├── uv.lock
└── README.md
```
