from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from agent_generic.state import AgentContext, RunState
from tools.abstract import DelegateSearchMetadata, FetchUrlMetadata, SearchWebMetadata, Tool, ToolExecutionResult


MAX_DELEGATED_QUERIES = 50
TODAY = datetime.now().strftime("%d %B %Y")



class SearchWebArgs(BaseModel):
    query: str


async def search_web(
    args: SearchWebArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    exa = context.exa
    if exa is None:
        return ToolExecutionResult(
            model_response={"error": "Exa client is not configured."}
        )

    results = exa.search(
        args.query,
        num_results=10,
        type="auto",
        contents={"highlights": {"max_characters": 4000}},
    )

    formatted_results: list[str] = []
    for item in results.results:
        highlights = item.highlights or []
        formatted_results.append(
            f"""
<result>
<title>{item.title or ""}</title>
<url>{item.url}</url>
<highlights>
{chr(10).join(f"- {highlight}" for highlight in highlights)}
</highlights>
</result>""".strip()
        )

    return ToolExecutionResult(
        model_response={
            "result": f"""
Search results for: {args.query}

<results>
{chr(10).join(formatted_results)}
</results>""".strip()
        },
        metadata=SearchWebMetadata(
            query=args.query,
            raw_results=results,
        ),
    )


class FetchUrlArgs(BaseModel):
    url: str = Field(
        ...,
        description="The exact URL to fetch full page content from.",
    )


async def fetch_url(
    args: FetchUrlArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state
    exa = context.exa
    if exa is None:
        return ToolExecutionResult(
            model_response={"error": "Exa client is not configured."}
        )

    response = exa.get_contents(
        [args.url],
        text=True,
        filter_empty_results=False,
    )

    if not response.results:
        return ToolExecutionResult(
            model_response={"error": f"No content returned for URL: {args.url}"}
        )

    result = response.results[0]
    text = (result.text or "").strip()
    char_count = len(text)

    return ToolExecutionResult(
        model_response={
            "result": f"""
Fetched content from: {args.url}

<title>{result.title or ""}</title>

<content>
{text}
</content>""".strip()
        },
        metadata=FetchUrlMetadata(
            url=args.url,
            text=text,
            char_count=char_count,
        ),
    )


class DelegateSearchArgs(BaseModel):
    queries: list[str] = Field(
        ...,
        min_length=1,
        max_length=MAX_DELEGATED_QUERIES,
        description=(
            "A set of distinct search questions that you need an answer to. "
            "Each item should be written as a question for a sub-agent to answer, "
            "and each question should cover a meaningfully different aspect of the "
            "user's request. For example, for the history of Google, ask separate "
            "questions like who the first founding members were, how the founders "
            "met, and why they chose the name Google."
        ),
    )

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, queries: list[str]) -> list[str]:
        normalized_queries: list[str] = []
        seen: set[str] = set()
        for query in queries:
            normalized = " ".join(query.split())
            if not normalized:
                raise ValueError("Queries must not be empty.")
            normalized_key = normalized.lower()
            if normalized_key in seen:
                raise ValueError("Queries must be distinct.")
            seen.add(normalized_key)
            normalized_queries.append(normalized)
        return normalized_queries


async def delegate_search(
    args: DelegateSearchArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    if context.search_agent_runner is None:
        return ToolExecutionResult(
            model_response={"error": "Search subagent runner is not configured."}
        )

    results = await context.search_agent_runner(args.queries)
    if not results:
        return ToolExecutionResult(
            model_response={"error": "Search subagent did not return any results."}
        )

    query_answers_xml = []
    for item in results:
        query_answers_xml.append(
            f"""
<query_answer>
<query>{item["query"]}</query>
<answer>
{item["answer"]}
</answer>
</query_answer>""".strip()
        )

    return ToolExecutionResult(
        model_response={
            "queries": list(args.queries),
            "results": results,
        },
        metadata=DelegateSearchMetadata(
            queries=list(args.queries),
            results=results,
        ),
    )


SEARCH_WEB_TOOL = Tool(
    name="search_web",
    description="Search the web with Exa and return cited results.",
    args_model=SearchWebArgs,
    handler=search_web,
)

FETCH_URL_TOOL = Tool(
    name="fetch_url",
    description="Fetch the full text content of a specific URL directly. Use this when you have an exact URL and need the complete page content rather than search snippets.",
    args_model=FetchUrlArgs,
    handler=fetch_url,
)

DELEGATE_SEARCH_TOOL = Tool(
    name="delegate_search",
    description="Delegate 1 to 3 distinct web research queries to search subagents.",
    args_model=DelegateSearchArgs,
    handler=delegate_search,
)
