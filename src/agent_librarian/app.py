"""Librarian Agent — answers topic queries by searching the local book database."""

import asyncio

from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))

from google.genai import Client, types
from rich import print
from rich.markdown import Markdown

from agent_generic.agent import Agent, render_message, render_tool_call
from agent_generic.state import AgentContext, RunConfig, RunState
from tools.abstract import (
    ListConvertedFilesMetadata,
    ListSourcePdfsMetadata,
    ListUnprocessedPdfsMetadata,
    SearchBooksMetadata,
    ToolExecutionResult,
)
from agent_librarian.instructions import LIBRARIAN_INSTRUCTION
from utils.sqlite_db.books_storage import DB_PATH
from tools.books_db_search import SEARCH_BOOKS_TOOL
from tools.pdf_paths import (
    LIST_CONVERTED_FILES_TOOL,
    LIST_SOURCE_PDFS_TOOL,
    LIST_UNPROCESSED_PDFS_TOOL,
)


# ---------------------------------------------------------------------------
# Tool result renderer
# ---------------------------------------------------------------------------

async def render_tool_result_librarian(
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
    if isinstance(metadata, SearchBooksMetadata):
        print()
        if metadata.total_found == 0:
            print(f"[yellow]No books found[/yellow] for query: [dim]{metadata.query}[/dim]")
        else:
            print(
                f"[green]Found {metadata.total_found} book(s)[/green] "
                f"for query: [dim]{metadata.query}[/dim]"
            )
            for title in metadata.titles:
                print(f"  • {title}")
    elif isinstance(metadata, ListSourcePdfsMetadata):
        print()
        print(
            f"[cyan]Source PDFs:[/cyan] {metadata.total} total, "
            f"{metadata.parsed} with parsed metadata"
        )

    elif isinstance(metadata, ListConvertedFilesMetadata):
        print()
        ext_label = metadata.ext_filter or "all"
        print(f"[cyan]Converted files ([/cyan]{ext_label}[cyan]):[/cyan] {metadata.total} found")

    elif isinstance(metadata, ListUnprocessedPdfsMetadata):
        print()
        print(
            f"[yellow]Unprocessed PDFs[/yellow] (no '{metadata.ext}' output): "
            f"{metadata.total} found"
        )

# ---------------------------------------------------------------------------
# Per-query answer cycle
# ---------------------------------------------------------------------------

async def answer_query(query: str, client: Client) -> None:
    """Run the librarian agent for a single topic query."""
    context = AgentContext()

    agent = Agent(
        client=client,
        config=RunConfig(
            model="gemini-3.1-pro-preview",
            thinking_level="LOW",
            max_iterations=10,
        ),
        state=RunState(mode="execute"),
        context=context,
        plan_tools=[],
        execute_tools=[
            SEARCH_BOOKS_TOOL,
            LIST_SOURCE_PDFS_TOOL,
            LIST_CONVERTED_FILES_TOOL,
            LIST_UNPROCESSED_PDFS_TOOL,
        ],
        plan_system_instruction=LIBRARIAN_INSTRUCTION,
        execute_system_instruction=LIBRARIAN_INSTRUCTION,
    )

    agent.on("message", render_message)
    agent.on("llm_tool_call", render_tool_call)
    agent.on("tool_result", render_tool_result_librarian)

    contents: list[types.Content] = [
        types.UserContent(parts=[types.Part.from_text(text=query)])
    ]
    await agent.run_until_idle(contents)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    client = Client()

    print()
    print(Markdown("# Librarian — Book Discovery Agent"))
    print(f"Database: [dim]{DB_PATH}[/dim]")
    print()

    while True:
        print()
        query = input("  Topic / question (leave empty to quit): ").strip()
        if not query:
            break

        print()
        print(f"[bold]Searching for:[/bold] {query}")
        print("─" * 60)

        await answer_query(query, client)

        print()
        print("─" * 60)
        print("[green]Done.[/green] Enter another topic or leave empty to quit.")

    print("\nGoodbye.")


if __name__ == "__main__":
    asyncio.run(main())
