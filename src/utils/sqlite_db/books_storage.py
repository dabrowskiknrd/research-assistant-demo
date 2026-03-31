"""SQLite storage backend for researched books.

Provides both write operations (save_book_sqlite, save_chapter_sqlite)
and read/search operations (search_books_sqlite).

Database location: data/database/books/books.db at the project root.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

_BASE = Path(__file__).parent

# src/utils/sqlite_db -> src/utils -> src -> project root
_DB_DIR = _BASE.parent.parent.parent / "data" / "database" / "books"

DB_PATH = _DB_DIR / "books.db"

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

# Columns stored directly on the books table.
# URLs live in the separate book_urls table (see _CREATE_BOOK_URLS).
_BOOK_COLS = [
    "title",
    "subtitle",
    "author",
    "publisher",
    "year",
    "edition",
    "pages",
    "doi",
    "isbn_ebook",
    "description",
    "processed_at",
]

_CREATE_BOOKS = """
CREATE TABLE IF NOT EXISTS books (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    title        TEXT NOT NULL,
    subtitle     TEXT,
    author       TEXT NOT NULL,
    publisher    TEXT,
    year         TEXT,
    edition      INTEGER,
    pages        INTEGER,
    doi          TEXT,
    isbn_ebook   TEXT,
    description  TEXT,
    processed_at TEXT,
    UNIQUE(title, author) ON CONFLICT REPLACE
)
"""

_CREATE_CHAPTERS = """
CREATE TABLE IF NOT EXISTS chapters (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id        INTEGER NOT NULL
                       REFERENCES books(id) ON DELETE CASCADE,
    chapter_number INTEGER,
    title          TEXT,
    description    TEXT,
    url            TEXT
)
"""

# category values: publisher_book_page | book_dedicated | github_repo | author_website
_CREATE_BOOK_URLS = """
CREATE TABLE IF NOT EXISTS book_urls (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    url         TEXT NOT NULL,
    author_name TEXT,
    label       TEXT
)
"""

# file_type values: pdf | converted | other
_CREATE_BOOK_PATHS = """
CREATE TABLE IF NOT EXISTS book_paths (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    book_id     INTEGER NOT NULL REFERENCES books(id) ON DELETE CASCADE,
    path        TEXT NOT NULL,
    file_type   TEXT NOT NULL DEFAULT 'other',
    added_at    TEXT,
    UNIQUE(book_id, path) ON CONFLICT REPLACE
)
"""

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def _get_conn() -> sqlite3.Connection:
    """Open a connection with FK enforcement and WAL journal mode."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    # Must be set per-connection; enables ON DELETE CASCADE et al.
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL allows concurrent reads while writing.
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute(_CREATE_BOOKS)
    conn.execute(_CREATE_CHAPTERS)
    conn.execute(_CREATE_BOOK_URLS)
    conn.execute(_CREATE_BOOK_PATHS)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

_VALID_URL_CATEGORIES = frozenset(
    {"publisher_book_page", "book_dedicated", "github_repo", "author_website"}
)


def _save_book_urls(
    conn: sqlite3.Connection, book_id: int, urls: list[dict]
) -> None:
    """Insert URL rows for a book. Old rows are removed by CASCADE on INSERT OR REPLACE."""
    rows = []
    for entry in urls:
        category = (entry.get("category") or "").strip()
        url = (entry.get("url") or "").strip()
        if not url or category not in _VALID_URL_CATEGORIES:
            continue
        author_name = entry.get("author_name") if category == "author_website" else None
        label = entry.get("label") or None
        rows.append((book_id, category, url, author_name, label))
    if rows:
        conn.executemany(
            "INSERT INTO book_urls (book_id, category, url, author_name, label)"
            " VALUES (?, ?, ?, ?, ?)",
            rows,
        )


def save_book_sqlite(data: dict) -> str:
    """Insert or replace a book and its URLs. Returns 'saved' or 'updated'."""
    data = dict(data)
    data["processed_at"] = datetime.now().isoformat()

    conn = _get_conn()
    try:
        with conn:  # auto-commits on success, rolls back on exception
            cur = conn.execute(
                "SELECT id FROM books WHERE lower(title)=lower(?) AND lower(author)=lower(?)",
                (data["title"], data["author"]),
            )
            exists = cur.fetchone() is not None

            col_list = ", ".join(f'"{c}"' for c in _BOOK_COLS)
            placeholders = ", ".join("?" for _ in _BOOK_COLS)
            values = [data.get(c) or "" if c != "edition" else data.get("edition") for c in _BOOK_COLS]

            # INSERT OR REPLACE deletes the old row first (cascading to chapters),
            # then inserts a fresh row; lastrowid is the new book id.
            cur = conn.execute(
                f"INSERT OR REPLACE INTO books ({col_list}) VALUES ({placeholders})",
                values,
            )
            book_id = cur.lastrowid

            raw_urls = data.get("urls", [])
            if isinstance(raw_urls, list):
                _save_book_urls(conn, book_id, raw_urls)
    finally:
        conn.close()

    return "updated" if exists else "saved"


def save_chapter_sqlite(data: dict) -> str:
    """Insert or replace a single chapter for an already-saved book.

    Returns:
        'saved'          — new chapter was written.
        'updated'        — an existing chapter with the same number was replaced.
        'book_not_found' — no book matched the given title + author.
    """
    book_title = (data.get("book_title") or "").strip()
    book_author = (data.get("book_author") or "").strip()
    chapter_number = data.get("chapter_number")
    title = data.get("title") or ""
    description = data.get("description") or ""
    url = data.get("url") or None

    conn = _get_conn()
    try:
        with conn:
            cur = conn.execute(
                "SELECT id FROM books WHERE lower(title)=lower(?) AND lower(author)=lower(?)",
                (book_title, book_author),
            )
            row = cur.fetchone()
            if row is None:
                return "book_not_found"
            book_id = row["id"]

            # Replace existing chapter with the same number (if numbered);
            # rowcount tells us whether a prior row was removed.
            replaced = False
            if chapter_number is not None:
                cur = conn.execute(
                    "DELETE FROM chapters WHERE book_id=? AND chapter_number=?",
                    (book_id, chapter_number),
                )
                replaced = cur.rowcount > 0

            conn.execute(
                "INSERT INTO chapters (book_id, chapter_number, title, description, url)"
                " VALUES (?, ?, ?, ?, ?)",
                (book_id, chapter_number, title, description, url),
            )
    finally:
        conn.close()

    return "updated" if replaced else "saved"


# ---------------------------------------------------------------------------
# Read / search operations
# ---------------------------------------------------------------------------

def search_books_sqlite(query: str) -> list[dict]:
    """Return books whose description or chapter descriptions contain the query terms.

    Each result dict contains:
      - id, title, subtitle, author, publisher, year, edition, description
      - matched_chapters: list of {chapter_number, title, description} for chapters that matched
    """
    if not DB_PATH.exists():
        return []

    terms = [t.strip() for t in query.split() if t.strip()]
    if not terms:
        return []

    conn = _get_conn()
    try:
        # --- Books whose own description matches ---
        book_like = " OR ".join(
            "lower(b.description) LIKE lower(?)" for _ in terms
        )
        book_params = [f"%{t}%" for t in terms]
        book_rows = conn.execute(
            f"""
            SELECT DISTINCT b.id, b.title, b.subtitle, b.author,
                            b.publisher, b.year, b.edition, b.description
            FROM books b
            WHERE {book_like}
            """,
            book_params,
        ).fetchall()

        # --- Books that have at least one chapter matching ---
        ch_like = " OR ".join(
            "lower(c.description) LIKE lower(?) OR lower(c.title) LIKE lower(?)"
            for _ in terms
        )
        ch_params = [val for t in terms for val in (f"%{t}%", f"%{t}%")]
        ch_rows = conn.execute(
            f"""
            SELECT DISTINCT b.id, b.title, b.subtitle, b.author,
                            b.publisher, b.year, b.edition, b.description
            FROM books b
            JOIN chapters c ON c.book_id = b.id
            WHERE {ch_like}
            """,
            ch_params,
        ).fetchall()

        # Merge unique books by id
        seen: set[int] = set()
        merged: list[sqlite3.Row] = []
        for row in list(book_rows) + list(ch_rows):
            if row["id"] not in seen:
                seen.add(row["id"])
                merged.append(row)

        if not merged:
            return []

        # Version of ch_like without table alias for direct chapter queries
        ch_like_inner = " OR ".join(
            "lower(description) LIKE lower(?) OR lower(title) LIKE lower(?)"
            for _ in terms
        )

        results: list[dict] = []
        for row in merged:
            book_id = row["id"]
            ch_match_params = [val for t in terms for val in (f"%{t}%", f"%{t}%")]
            matching_chapters = conn.execute(
                f"""
                SELECT chapter_number, title, description
                FROM chapters
                WHERE book_id = ?
                  AND ({ch_like_inner})
                ORDER BY chapter_number
                """,
                [book_id] + ch_match_params,
            ).fetchall()

            results.append(
                {
                    "id": book_id,
                    "title": row["title"] or "",
                    "subtitle": row["subtitle"] or "",
                    "author": row["author"] or "",
                    "publisher": row["publisher"] or "",
                    "year": row["year"] or "",
                    "edition": row["edition"],
                    "description": row["description"] or "",
                    "matched_chapters": [
                        {
                            "chapter_number": ch["chapter_number"],
                            "title": ch["title"] or "",
                            "description": ch["description"] or "",
                        }
                        for ch in matching_chapters
                    ],
                }
            )

        return results
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Book-paths operations
# ---------------------------------------------------------------------------

_VALID_FILE_TYPES = frozenset({"pdf", "converted", "other"})


def _resolve_file_type(path: str, explicit: str | None) -> str:
    """Infer file_type from path suffix when not provided explicitly."""
    if explicit and explicit in _VALID_FILE_TYPES:
        return explicit
    suffix = Path(path).suffix.lower()
    if suffix == ".pdf":
        return "pdf"
    if suffix in {".mmd", ".md", ".html", ".docx", ".tex"}:
        return "converted"
    return "other"


def save_book_path(book_title: str, book_author: str, path: str, file_type: str | None = None) -> str:
    """Associate a file path with a book.

    Parameters
    ----------
    book_title:
        Title of the book exactly as stored in the database.
    book_author:
        Author(s) exactly as stored in the database.
    path:
        File path to associate (absolute or relative).
    file_type:
        One of ``'pdf'``, ``'converted'``, ``'other'``. Inferred from the
        file suffix when *None*.

    Returns
    -------
    ``'saved'``          — new mapping was written.
    ``'updated'``        — an existing mapping for the same (book, path) was replaced.
    ``'book_not_found'`` — no book matched book_title + book_author.
    """
    resolved_type = _resolve_file_type(path, file_type)
    added_at = datetime.now().isoformat()

    conn = _get_conn()
    try:
        with conn:
            cur = conn.execute(
                "SELECT id FROM books WHERE lower(title)=lower(?) AND lower(author)=lower(?)",
                (book_title, book_author),
            )
            row = cur.fetchone()
            if row is None:
                return "book_not_found"
            book_id = row["id"]

            cur = conn.execute(
                "SELECT id FROM book_paths WHERE book_id=? AND path=?",
                (book_id, path),
            )
            exists = cur.fetchone() is not None

            conn.execute(
                "INSERT OR REPLACE INTO book_paths (book_id, path, file_type, added_at)"
                " VALUES (?, ?, ?, ?)",
                (book_id, path, resolved_type, added_at),
            )
    finally:
        conn.close()

    return "updated" if exists else "saved"


def get_paths_for_book(book_title: str, book_author: str) -> list[dict]:
    """Return all file paths associated with a book.

    Each dict has keys: ``id``, ``path``, ``file_type``, ``added_at``.
    Returns an empty list when the book is not found or has no paths.
    """
    if not DB_PATH.exists():
        return []

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            SELECT bp.id, bp.path, bp.file_type, bp.added_at
            FROM book_paths bp
            JOIN books b ON b.id = bp.book_id
            WHERE lower(b.title)=lower(?) AND lower(b.author)=lower(?)
            ORDER BY bp.file_type, bp.path
            """,
            (book_title, book_author),
        )
        return [
            {
                "id": row["id"],
                "path": row["path"],
                "file_type": row["file_type"],
                "added_at": row["added_at"],
            }
            for row in cur.fetchall()
        ]
    finally:
        conn.close()


def get_book_for_path(path: str) -> dict | None:
    """Return the book record associated with the given file path, or *None*.

    The returned dict contains the same fields as a row from the books table:
    ``id``, ``title``, ``subtitle``, ``author``, ``publisher``, ``year``,
    ``edition``, ``description``.
    """
    if not DB_PATH.exists():
        return None

    conn = _get_conn()
    try:
        cur = conn.execute(
            """
            SELECT b.id, b.title, b.subtitle, b.author, b.publisher,
                   b.year, b.edition, b.description
            FROM book_paths bp
            JOIN books b ON b.id = bp.book_id
            WHERE bp.path = ?
            """,
            (path,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return {
            "id": row["id"],
            "title": row["title"] or "",
            "subtitle": row["subtitle"] or "",
            "author": row["author"] or "",
            "publisher": row["publisher"] or "",
            "year": row["year"] or "",
            "edition": row["edition"],
            "description": row["description"] or "",
        }
    finally:
        conn.close()


def delete_book_path(book_title: str, book_author: str, path: str) -> bool:
    """Remove a specific path mapping for a book.

    Returns ``True`` if a row was deleted, ``False`` if nothing matched.
    """
    conn = _get_conn()
    try:
        with conn:
            cur = conn.execute(
                """
                DELETE FROM book_paths
                WHERE path = ?
                  AND book_id = (
                      SELECT id FROM books
                      WHERE lower(title)=lower(?) AND lower(author)=lower(?)
                  )
                """,
                (path, book_title, book_author),
            )
            return cur.rowcount > 0
    finally:
        conn.close()
