"""Paths-based Librarian Research Agent.

Accepts a file path of the form:
    <Title [optional edition]> - <Author(s)>, <Publisher>, <Year>.<ext>

Parses the path, then runs the same plan → execute → save research cycle as
agent_lib_assistant, saving the result to the local SQLite database.
"""

import asyncio
import os
import re
from dataclasses import dataclass
from pathlib import Path

from dotenv import find_dotenv, load_dotenv
from exa_py import Exa

load_dotenv(find_dotenv(raise_error_if_not_found=False))

from google.genai import Client, types
from rich import box, print
from rich.live import Live
from rich.markdown import Markdown
from rich.table import Table

from agent_generic.agent import Agent, render_message, render_tool_call
from agent_generic.state import AgentContext, RunConfig, RunState
from tools.abstract import (
    GeneratePlanMetadata,
    SaveBookMetadata,
    SaveBookPathMetadata,
    SaveChapterMetadata,
    ToolExecutionResult,
)
from tools.base import GENERATE_PLAN_TOOL, MODIFY_TODO_TOOL
from tools.web_search import DELEGATE_SEARCH_TOOL, FETCH_URL_TOOL, SEARCH_WEB_TOOL
from tools.books_db_save import SAVE_BOOK_PATH_TOOL, SAVE_BOOK_TOOL, SAVE_CHAPTER_TOOL
from agent_librarian_assistant.instructions import (
    PATHS_EXECUTE_INSTRUCTION,
    PATHS_PLAN_INSTRUCTION,
    PATHS_SEARCH_SUBAGENT_INSTRUCTION,
)
from utils.sqlite_db.books_storage import DB_PATH

# ---------------------------------------------------------------------------
# Path parsing
# ---------------------------------------------------------------------------

# Matches: <stem> - <author>, <publisher>, <year>
_STEM_RE = re.compile(
    r"^(?P<title>.+?)\s+-\s+(?P<author>.+?),\s*(?P<publisher>.+?),\s*(?P<year>\d{4})$"
)

# Matches an edition marker at the end of a title, e.g. "3rd ED", "4th Edition", "2nd EDITION"
_EDITION_RE = re.compile(
    r"\s+(?P<edition>\d+(?:st|nd|rd|th)\s+(?:EDITION|ED)|(?:EDITION|ED)\s+\d+)\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedInput:
    raw_path: str
    file_path: Path
    suffix: str          # e.g. ".pdf", ".md", ".mmd"
    title: str           # clean title, edition marker stripped
    edition_str: str     # e.g. "3rd ED" — empty string when not present
    author: str
    publisher: str
    year: str


def parse_path_input(raw: str) -> ParsedInput | None:
    """Parse a filename / path that follows the project naming convention.

    Expected stem format::

        <Title [optional edition]> - <Author(s)>, <Publisher>, <Year>

    Returns *None* if the input does not match.
    """
    path = Path(raw.strip())
    stem = path.stem
    suffix = path.suffix.lower()

    match = _STEM_RE.match(stem)
    if match is None:
        return None

    raw_title = match.group("title").strip()
    author = match.group("author").strip()
    publisher = match.group("publisher").strip()
    year = match.group("year").strip()

    # Try to split the edition marker out of the title
    edition_match = _EDITION_RE.search(raw_title)
    if edition_match:
        edition_str = edition_match.group("edition").strip()
        title = raw_title[: edition_match.start()].strip()
    else:
        edition_str = ""
        title = raw_title

    return ParsedInput(
        raw_path=raw,
        file_path=path,
        suffix=suffix,
        title=title,
        edition_str=edition_str,
        author=author,
        publisher=publisher,
        year=year,
    )


# ---------------------------------------------------------------------------
# Rich helpers
# ---------------------------------------------------------------------------

def truncate_cell(text: str, max_length: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= max_length:
        return normalized
    return f"{normalized[: max_length - 4].rstrip()}...."


def render_subagent_table(statuses: dict[str, str]) -> Table:
    table = Table(title="Search Subagents", box=box.SQUARE, show_lines=True)
    table.add_column("Query", no_wrap=False)
    table.add_column("Latest Action", no_wrap=True)
    for query, status in statuses.items():
        table.add_row(query, truncate_cell(status, 72))
    return table


# ---------------------------------------------------------------------------
# Tool result renderer
# ---------------------------------------------------------------------------

async def render_tool_result(
    call: types.FunctionCall,
    result: ToolExecutionResult,
    config: RunConfig,
    state: RunState,
    context: AgentContext,
) -> None:
    error = result.model_response.get("error")
    if error:
        print()
        print(f"[red]Tool error ({call.name}):[/red] {error}")
        return

    metadata = result.metadata

    if isinstance(metadata, GeneratePlanMetadata):
        print()
        print("[green]Plan created — switching to execute mode.[/green]")
        for todo in metadata.todos:
            print(f"  • {todo}")
        return

    if isinstance(metadata, SaveBookMetadata):
        icon = "✓" if metadata.status == "saved" else "↺"
        label = "Saved" if metadata.status == "saved" else "Updated"
        print()
        print(
            f"[green]{icon} {label}:[/green] "
            f"[bold]{metadata.title}[/bold] — {metadata.author}"
        )
        saved = result.model_response.get("saved_fields", [])
        missing = result.model_response.get("missing_fields", [])
        url_categories = result.model_response.get("url_categories", [])
        if url_categories:
            print(f"  [dim]URLs saved:[/dim] {', '.join(url_categories)}")
        if missing:
            print(f"  [yellow]Missing fields:[/yellow] {', '.join(missing)}")
        return

    if isinstance(metadata, SaveChapterMetadata):
        icon = "✓" if metadata.status == "saved" else "↺"
        label = "Saved" if metadata.status == "saved" else "Updated"
        chapter_label = (
            f"Ch.{metadata.chapter_number}"
            if metadata.chapter_number is not None
            else "Unnumbered chapter"
        )
        print()
        print(
            f"[green]{icon} {label} {chapter_label}:[/green] "
            f"[bold]{metadata.title}[/bold] "
            f"— [dim]{metadata.book_title}[/dim]"
        )
        if metadata.url_saved:
            print("  [dim]URL saved[/dim]")
        return

    if isinstance(metadata, SaveBookPathMetadata):
        if metadata.status == "book_not_found":
            return  # error already printed above
        icon = "✓" if metadata.status == "saved" else "↺"
        label = "Linked" if metadata.status == "saved" else "Re-linked"
        print()
        print(
            f"[cyan]{icon} {label} path:[/cyan] [dim]{metadata.path}[/dim]"
        )
        print(
            f"  → [bold]{metadata.book_title}[/bold] — {metadata.book_author}"
        )
        return


# ---------------------------------------------------------------------------
# Search subagent
# ---------------------------------------------------------------------------

async def run_search_subagent(
    exa: Exa,
    query: str,
    context: AgentContext,
) -> dict[str, str]:
    child_agent = Agent(
        client=Client(),
        config=RunConfig(
            model="gemini-3.1-flash-lite-preview",
            thinking_level="LOW",
            max_iterations=4,
        ),
        state=RunState(mode="execute"),
        context=AgentContext(
            exa=exa,
            live=context.live,
            subagent_statuses=context.subagent_statuses,
        ),
        plan_tools=[],
        execute_tools=[SEARCH_WEB_TOOL, FETCH_URL_TOOL],
        plan_system_instruction=PATHS_SEARCH_SUBAGENT_INSTRUCTION,
        execute_system_instruction=PATHS_SEARCH_SUBAGENT_INSTRUCTION,
    )

    async def update_status(
        call: types.FunctionCall,
        config: RunConfig,
        state: RunState,
        context: AgentContext,
    ) -> None:
        if call.name == "search_web" and call.args and "query" in call.args:
            context.subagent_statuses[query] = f"search_web: {call.args['query']}"
        else:
            context.subagent_statuses[query] = f"Calling {call.name}"
        if context.live is not None:
            context.live.update(render_subagent_table(context.subagent_statuses))

    child_agent.on("llm_tool_call", update_status)

    context.subagent_statuses[query] = "Starting"
    if context.live is not None:
        context.live.update(render_subagent_table(context.subagent_statuses))

    child_contents: list[types.Content] = [
        types.UserContent(parts=[types.Part.from_text(text=query)])
    ]
    final_message = await child_agent.run_until_idle(child_contents)
    final_text = "\n".join(
        part.text for part in final_message.parts if part.text
    ).strip()
    context.subagent_statuses[query] = final_text[:100] or "Done"
    if context.live is not None:
        context.live.update(render_subagent_table(context.subagent_statuses))
    return {"query": query, "answer": final_text}


async def run_search_subagents(
    exa: Exa,
    queries: list[str],
    context: AgentContext,
    max_concurrent: int = 10,
) -> list[dict[str, str]]:
    context.subagent_statuses = {query: "Queued" for query in queries}
    semaphore = asyncio.Semaphore(max_concurrent)

    async def run_with_semaphore(query: str) -> dict[str, str]:
        async with semaphore:
            return await run_search_subagent(exa, query, context)

    with Live(
        render_subagent_table(context.subagent_statuses), refresh_per_second=8
    ) as live:
        context.live = live
        results: list[dict[str, str]] = await asyncio.gather(
            *[run_with_semaphore(query) for query in queries]
        )
        live.update(render_subagent_table(context.subagent_statuses))
        context.live = None
        return list(results)


# ---------------------------------------------------------------------------
# Per-source research cycle
# ---------------------------------------------------------------------------

async def research_source(parsed: ParsedInput, exa: Exa, client: Client) -> None:
    """Run a full plan → execute → save cycle for a single parsed source."""
    context = AgentContext(
        exa=exa,
        search_agent_runner=lambda queries: run_search_subagents(exa, queries, context),
    )

    agent = Agent(
        client=client,
        config=RunConfig(
            model="gemini-3.1-pro-preview",
            thinking_level="LOW",
            max_iterations=40,
        ),
        state=RunState(mode="plan"),
        context=context,
        plan_tools=[GENERATE_PLAN_TOOL],
        execute_tools=[
            MODIFY_TODO_TOOL,
            DELEGATE_SEARCH_TOOL,
            SAVE_BOOK_TOOL,
            SAVE_CHAPTER_TOOL,
            SAVE_BOOK_PATH_TOOL,
        ],
        plan_system_instruction=PATHS_PLAN_INSTRUCTION,
        execute_system_instruction=PATHS_EXECUTE_INSTRUCTION,
    )

    agent.on("message", render_message)
    agent.on("llm_tool_call", render_tool_call)
    agent.on("tool_result", render_tool_result)

    edition_note = f", Edition string: {parsed.edition_str!r}" if parsed.edition_str else ""
    source_info = (
        f'Title: "{parsed.title}"{edition_note}, '
        f"Author: {parsed.author}, "
        f"Publisher: {parsed.publisher}, "
        f"Year: {parsed.year}, "
        f"File: {parsed.raw_path}"
    )

    contents: list[types.Content] = [
        types.UserContent(
            parts=[types.Part.from_text(text=f"Research this source: {source_info}")]
        )
    ]
    await agent.run_until_idle(contents)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def prompt_path() -> str | None:
    """Prompt for a file path. Returns None when the user wants to quit."""
    print()
    raw = input(
        "  File path (e.g. data/sources/pdfs/Title - Author, Publisher, Year.pdf)\n"
        "  Leave empty to quit: "
    ).strip()
    return raw or None


async def main() -> None:
    exa_api_key = os.getenv("EXA_API_KEY")
    if not exa_api_key:
        raise RuntimeError("EXA_API_KEY environment variable is required.")

    exa = Exa(api_key=exa_api_key)
    client = Client()

    print()
    print(Markdown("# Paths-based Librarian Research Agent"))
    print(f"Database: [dim]{DB_PATH}[/dim]")
    print()
    print(
        "Enter a file path following the naming convention:\n"
        "  [dim]<Title [Edition]> - <Author(s)>, <Publisher>, <Year>.<ext>[/dim]"
    )

    while True:
        raw = prompt_path()
        if raw is None:
            break

        parsed = parse_path_input(raw)
        if parsed is None:
            print(
                "[red]Could not parse path.[/red] "
                "Expected: [dim]Title [Edition] - Author(s), Publisher, Year.ext[/dim]"
            )
            continue

        print()
        print(f"  [bold]Title:[/bold]     {parsed.title}")
        if parsed.edition_str:
            print(f"  [bold]Edition:[/bold]   {parsed.edition_str}")
        print(f"  [bold]Author:[/bold]    {parsed.author}")
        print(f"  [bold]Publisher:[/bold] {parsed.publisher}")
        print(f"  [bold]Year:[/bold]      {parsed.year}")
        print(f"  [bold]Format:[/bold]    {parsed.suffix or '(no extension)'}")
        print()
        print("─" * 60)

        await research_source(parsed, exa, client)

        print()
        print("─" * 60)
        print("[green]Done.[/green] Enter another path or leave empty to quit.")

    print("\nGoodbye.")


if __name__ == "__main__":
    asyncio.run(main())
