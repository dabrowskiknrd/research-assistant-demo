"""Tool for the Library Boss agent: search the local book database by topic."""

import json

from pydantic import BaseModel, Field

from agent_generic.state import AgentContext, RunState
from tools.abstract import SearchBooksMetadata, Tool, ToolExecutionResult
from utils.sqlite_db.books_storage import search_books_sqlite



class SearchBooksArgs(BaseModel):
    query: str = Field(
        ...,
        description=(
            "Topic or keyword query to search against book descriptions "
            "and chapter descriptions in the local database."
        ),
    )


async def search_books(
    args: SearchBooksArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    results = search_books_sqlite(args.query)

    if not results:
        return ToolExecutionResult(
            model_response={
                "query": args.query,
                "total_found": 0,
                "books": [],
                "result": f"No books found in the database matching: {args.query}",
            },
            metadata=SearchBooksMetadata(
                query=args.query,
                total_found=0,
                titles=[],
            ),
        )

    books_xml: list[str] = []
    for book in results:
        edition_str = f" (Edition {book['edition']})" if book["edition"] else ""
        subtitle_str = f": {book['subtitle']}" if book["subtitle"] else ""
        year_str = f" [{book['year']}]" if book["year"] else ""
        publisher_str = f", {book['publisher']}" if book["publisher"] else ""

        matched_ch_lines: list[str] = []
        for ch in book["matched_chapters"]:
            num = f"Ch.{ch['chapter_number']} — " if ch["chapter_number"] is not None else ""
            desc_snippet = ch["description"][:200].rstrip() if ch["description"] else ""
            if desc_snippet and len(ch["description"]) > 200:
                desc_snippet += "…"
            matched_ch_lines.append(
                f"  • {num}{ch['title']}"
                + (f": {desc_snippet}" if desc_snippet else "")
            )

        chapters_section = (
            "\n<matched_chapters>\n"
            + "\n".join(matched_ch_lines)
            + "\n</matched_chapters>"
            if matched_ch_lines
            else ""
        )

        desc_snippet = book["description"][:400].rstrip() if book["description"] else ""
        if desc_snippet and len(book["description"]) > 400:
            desc_snippet += "…"

        books_xml.append(
            f"""<book>
<title>{book['title']}{subtitle_str}{edition_str}{year_str}</title>
<author>{book['author']}{publisher_str}</author>
<description>{desc_snippet}</description>{chapters_section}
</book>"""
        )

    result_text = (
        f"Found {len(results)} book(s) matching '{args.query}':\n\n"
        + "\n\n".join(books_xml)
    )

    return ToolExecutionResult(
        model_response={
            "query": args.query,
            "total_found": len(results),
            "books": [
                {
                    "title": b["title"],
                    "author": b["author"],
                    "year": b["year"],
                    "publisher": b["publisher"],
                    "edition": b["edition"],
                    "description": b["description"],
                    "matched_chapters": b["matched_chapters"],
                }
                for b in results
            ],
            "result": result_text,
        },
        metadata=SearchBooksMetadata(
            query=args.query,
            total_found=len(results),
            titles=[b["title"] for b in results],
        ),
    )


SEARCH_BOOKS_TOOL = Tool(
    name="search_books",
    description=(
        "Search the local library database for books relevant to a topic. "
        "Matches against book descriptions and individual chapter descriptions. "
        "Returns a list of matching books with relevant chapter excerpts."
    ),
    args_model=SearchBooksArgs,
    handler=search_books,
)
