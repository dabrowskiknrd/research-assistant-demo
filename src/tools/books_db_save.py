"""Tools for the paths-based librarian agent.

Provides three tools:
  - save_book        — save a fully researched book record to the SQLite database
  - save_chapter     — save a single chapter for an already-saved book
  - save_book_path   — record the file-path → book mapping in book_paths
"""

import json
from typing import Any

from pydantic import BaseModel, Field

from agent_generic.state import AgentContext, RunState
from tools.abstract import (
    SaveBookMetadata,
    SaveBookPathMetadata,
    SaveChapterMetadata,
    Tool,
    ToolExecutionResult,
)
from utils.sqlite_db.books_storage import save_book_path, save_book_sqlite, save_chapter_sqlite



class SaveBookArgs(BaseModel):
    title: str = Field(
        ...,
        description="Title of the book.",
    )
    subtitle: str = Field(
        default="",
        description="Subtitle of the book, if any.",
    )
    author: str = Field(
        ...,
        description="Author(s). Comma-separated for multiple authors.",
    )
    publisher: str = Field(
        default="",
        description="Publisher name.",
    )
    year: str = Field(
        default="",
        description="Year of publication.",
    )
    edition: int | None = Field(
        default=None,
        description="Edition number as an integer (e.g. 1, 2, 3). Infer from labels like '2nd', 'Third', 'Revised 4th' etc. Use null if no edition information is found.",
    )
    pages: str = Field(
        default="",
        description="Total number of pages as a string (e.g. '432'). Leave empty if unknown.",
    )
    doi: str = Field(
        default="",
        description="DOI of the book or paper (e.g. '10.1145/1234567'). Leave empty if not applicable.",
    )
    isbn_ebook: str = Field(
        default="",
        description="eBook ISBN (ISBN-13 preferred). Leave empty if not found.",
    )
    urls_json: str = Field(
        default="[]",
        description=(
            "JSON array of URL objects found for this source. Each object must have:\n"
            '  - "category": one of "publisher_book_page", "book_dedicated", '
            '"github_repo", "author_website"\n'
            '  - "url": the full URL string\n'
            '  - "author_name": the author\'s full name (only for category="author_website")\n'
            '  - "label": optional short description of the URL\n'
            "Include one entry per URL found across all categories. Use [] if none found.\n"
            'Example: [{"category": "publisher_book_page", '
            '"url": "https://nostarch.com/python-crash-course", '
            '"label": "No Starch Press book page"}, '
            '{"category": "github_repo", '
            '"url": "https://github.com/ehmatthes/pcc", "label": "Code examples"}, '
            '{"category": "author_website", "url": "https://ehmatthes.github.io", '
            '"author_name": "Eric Matthes", "label": "Author site"}]'
        ),
    )
    description: str = Field(
        default="",
        description="A concise 2-4 sentence description of the source.",
    )

async def save_book_handler(
    args: SaveBookArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    try:
        urls: list[Any] = json.loads(args.urls_json)
        if not isinstance(urls, list):
            urls = []
    except (json.JSONDecodeError, ValueError):
        urls = []

    data = {
        "title": args.title,
        "subtitle": args.subtitle,
        "author": args.author,
        "publisher": args.publisher,
        "year": args.year,
        "edition": args.edition if args.edition is not None else None,
        "pages": args.pages,
        "doi": args.doi,
        "isbn_ebook": args.isbn_ebook,
        "description": args.description,
        "urls": urls,
    }

    try:
        status = save_book_sqlite(data)
    except Exception as exc:
        return ToolExecutionResult(
            model_response={"error": f"Failed to save source: {exc}"}
        )

    scalar_fields = {k: v for k, v in data.items() if k != "urls"}
    saved_fields = [k for k, v in scalar_fields.items() if v]
    missing_fields = [k for k, v in scalar_fields.items() if not v]
    url_categories = [u.get("category", "") for u in urls if isinstance(u, dict)]

    return ToolExecutionResult(
        model_response={
            "result": (
                f"Source '{args.title}' by {args.author} was {status} in the database."
            ),
            "status": status,
            "saved_fields": saved_fields,
            "missing_fields": missing_fields,
            "urls_saved": len(urls),
            "url_categories": url_categories,
        },
        metadata=SaveBookMetadata(
            title=args.title,
            author=args.author,
            status=status,
            url_count=len(urls),
        ),
    )


SAVE_BOOK_TOOL = Tool(
    name="save_book",
    description=(
        "Save a fully researched book, article, lecture, or paper to the "
        "local SQLite database. Call this once you have gathered all available "
        "information. Provide chapters as a JSON array string in chapters_json and "
        "all found URLs (publisher book page, book-dedicated site, GitHub repo, "
        "author websites) as a JSON array string in urls_json."
    ),
    args_model=SaveBookArgs,
    handler=save_book_handler,
)


# ---------------------------------------------------------------------------
# save_chapter tool
# ---------------------------------------------------------------------------


class SaveChapterArgs(BaseModel):
    book_title: str = Field(
        ...,
        description="Title of the book this chapter belongs to (must match exactly as saved).",
    )
    book_author: str = Field(
        ...,
        description="Author(s) of the book, exactly as stored in the database.",
    )
    chapter_number: int | None = Field(
        default=None,
        description="Chapter number as an integer (e.g. 1, 2, 3). Use null if the chapter is not numbered.",
    )
    title: str = Field(
        ...,
        description="Chapter title.",
    )
    description: str = Field(
        default="",
        description=(
            "Chapter description or abstract. MUST be copied verbatim from the "
            "publisher page — do not paraphrase, shorten, or rewrite it. "
            "Leave empty only if no description was found."
        ),
    )
    url: str = Field(
        default="",
        description="URL of the chapter page (e.g. a SpringerLink chapter URL). Leave empty if not available.",
    )


async def save_chapter_handler(
    args: SaveChapterArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    try:
        status = save_chapter_sqlite(args.model_dump())
    except Exception as exc:
        return ToolExecutionResult(
            model_response={"error": f"Failed to save chapter: {exc}"}
        )

    if status == "book_not_found":
        return ToolExecutionResult(
            model_response={
                "error": (
                    f"Book '{args.book_title}' by {args.book_author} not found "
                    "in the database. Save the book first using save_book."
                )
            }
        )

    chapter_label = f"Chapter {args.chapter_number}" if args.chapter_number is not None else "Unnumbered chapter"
    action = "updated" if status == "updated" else "saved"
    return ToolExecutionResult(
        model_response={
            "result": f"{chapter_label} '{args.title}' {action} for '{args.book_title}'.",
            "status": status,
            "chapter_number": args.chapter_number,
            "title": args.title,
            "url_saved": bool(args.url),
        },
        metadata=SaveChapterMetadata(
            book_title=args.book_title,
            book_author=args.book_author,
            chapter_number=args.chapter_number,
            title=args.title,
            status=status,
            url_saved=bool(args.url),
        ),
    )


SAVE_CHAPTER_TOOL = Tool(
    name="save_chapter",
    description=(
        "Save a single chapter (with its description and optional URL) for a book "
        "that has already been saved with save_book. Use this to persist chapter data "
        "one chapter at a time as you explore individual chapter pages. "
        "If a chapter with the same number already exists it will be replaced."
    ),
    args_model=SaveChapterArgs,
    handler=save_chapter_handler,
)


# ---------------------------------------------------------------------------
# save_book_path tool
# ---------------------------------------------------------------------------


class SaveBookPathArgs(BaseModel):
    book_title: str = Field(
        ...,
        description=(
            "Title of the book exactly as it was passed to save_book."
        ),
    )
    book_author: str = Field(
        ...,
        description=(
            "Author(s) exactly as stored in the database (i.e. exactly as passed to save_book)."
        ),
    )
    path: str = Field(
        ...,
        description=(
            "File path to associate with the book. "
            "Use the original path provided by the user (absolute or relative). "
            "Examples: 'data/sources/pdfs/Title - Author, Publisher, Year.pdf', "
            "'data/sources/pdfs_converted/Title - Author, Publisher, Year.md'."
        ),
    )
    file_type: str = Field(
        default="",
        description=(
            "Optional file type: 'pdf', 'converted', or 'other'. "
            "Leave empty to auto-detect from the file extension "
            "(.pdf → 'pdf'; .mmd/.md/.html/.docx/.tex → 'converted'; everything else → 'other')."
        ),
    )


async def save_book_path_handler(
    args: SaveBookPathArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    explicit_type = args.file_type.strip() or None
    status = save_book_path(
        book_title=args.book_title,
        book_author=args.book_author,
        path=args.path,
        file_type=explicit_type,
    )

    if status == "book_not_found":
        return ToolExecutionResult(
            model_response={
                "error": (
                    f"Book '{args.book_title}' by {args.book_author} was not found "
                    "in the database. Call save_book before save_book_path."
                )
            },
            metadata=SaveBookPathMetadata(
                book_title=args.book_title,
                book_author=args.book_author,
                path=args.path,
                file_type=explicit_type or "auto",
                status=status,
            ),
        )

    action = "Saved" if status == "saved" else "Updated"
    return ToolExecutionResult(
        model_response={
            "result": (
                f"{action} path mapping: '{args.path}' → "
                f"'{args.book_title}' by {args.book_author}."
            ),
            "status": status,
            "path": args.path,
            "book_title": args.book_title,
            "book_author": args.book_author,
        },
        metadata=SaveBookPathMetadata(
            book_title=args.book_title,
            book_author=args.book_author,
            path=args.path,
            file_type=explicit_type or "auto",
            status=status,
        ),
    )


SAVE_BOOK_PATH_TOOL = Tool(
    name="save_book_path",
    description=(
        "Record the file-path → book mapping in the database. "
        "Call this after save_book has successfully saved the book record. "
        "Provide the original file path as given by the user, the book title and author "
        "exactly as passed to save_book, and optionally the file_type "
        "('pdf', 'converted', or 'other'). "
        "The file type is inferred automatically from the extension when left empty."
    ),
    args_model=SaveBookPathArgs,
    handler=save_book_path_handler,
)
