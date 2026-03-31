from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Literal, TypeAlias, TypeVar

from google.genai import types
from pydantic import BaseModel

from agent_generic.state import AgentContext, RunState


ArgsT = TypeVar("ArgsT", bound=BaseModel)


@dataclass(slots=True)
class ReadFileMetadata:
    path: str
    contents: str


@dataclass(slots=True)
class WriteFileMetadata:
    path: str
    contents: str


@dataclass(slots=True)
class EditFileMetadata:
    path: str
    old_text: str
    new_text: str


@dataclass(slots=True)
class ModifyTodoMetadata:
    action: Literal["add", "remove"]
    todos: list[str]


@dataclass(slots=True)
class GeneratePlanMetadata:
    todos: list[str]


@dataclass(slots=True)
class BashMetadata:
    command: str
    returncode: int
    stdout: str
    stderr: str


@dataclass(slots=True)
class SaveBookMetadata:
    title: str
    author: str
    status: str  # "saved" | "updated"
    url_count: int


@dataclass(slots=True)
class SaveChapterMetadata:
    book_title: str
    book_author: str
    chapter_number: int | None
    title: str
    status: str  # "saved" | "updated"
    url_saved: bool


@dataclass(slots=True)
class SaveBookPathMetadata:
    book_title: str
    book_author: str
    path: str
    file_type: str
    status: str  # "saved" | "updated" | "book_not_found"


@dataclass(slots=True)
class SearchBooksMetadata:
    query: str
    total_found: int
    titles: list[str]


@dataclass(slots=True)
class ConvertPdfMetadata:
    source: str          # file path or URL
    pdf_id: str
    num_pages: int | None
    output_formats: list[str]
    mmd_chars: int       # character count of the mmd text (0 if not requested)


@dataclass(slots=True)
class ListSourcePdfsMetadata:
    total: int
    parsed: int  # files that matched the naming convention


@dataclass(slots=True)
class ListConvertedFilesMetadata:
    total: int
    ext_filter: str | None

@dataclass(slots=True)
class ListUnprocessedPdfsMetadata:
    total: int
    ext: str


@dataclass(slots=True)
class ReadConvertedFileMetadata:
    filename: str
    char_count: int


@dataclass(slots=True)
class SearchWebMetadata:
    query: str
    raw_results: Any


@dataclass(slots=True)
class FetchUrlMetadata:
    url: str
    text: str
    char_count: int


@dataclass(slots=True)
class DelegateSearchMetadata:
    queries: list[str]
    results: list[dict[str, str]]


@dataclass(slots=True)
class SQLiteWriteMetadata:
    db_path: str
    table: str
    data: dict[str, Any]
    rows_affected: int


@dataclass(slots=True)
class SQLiteQueryMetadata:
    db_path: str
    query: str
    row_count: int


ToolMetadata: TypeAlias = (
    ReadFileMetadata
    | WriteFileMetadata
    | EditFileMetadata
    | ModifyTodoMetadata
    | GeneratePlanMetadata
    | BashMetadata
    | SaveBookMetadata
    | SaveChapterMetadata
    | SaveBookPathMetadata
    | SearchBooksMetadata
    | ConvertPdfMetadata
    | ListSourcePdfsMetadata
    | ListConvertedFilesMetadata
    | ListUnprocessedPdfsMetadata
    | ReadConvertedFileMetadata
    | SearchWebMetadata
    | FetchUrlMetadata
    | DelegateSearchMetadata
    | SQLiteWriteMetadata
    | SQLiteQueryMetadata
)


@dataclass(slots=True)
class ToolExecutionResult:
    model_response: dict[str, Any]
    metadata: ToolMetadata | None = None


ToolHandler = Callable[[ArgsT, RunState, AgentContext], Awaitable[ToolExecutionResult]]


@dataclass(slots=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: ToolHandler

    def to_genai_tool(self) -> types.Tool:
        schema = self.args_model.model_json_schema()
        return types.Tool(
            function_declarations=[
                types.FunctionDeclaration(
                    name=self.name,
                    description=self.description,
                    parameters=types.Schema(
                        type="OBJECT",
                        properties=schema["properties"],
                        required=schema.get("required", []),
                    ),
                )
            ]
        )
