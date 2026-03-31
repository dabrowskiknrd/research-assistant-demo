import asyncio
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agent_generic.state import AgentContext, RunState
from tools.abstract import (
    BashMetadata,
    EditFileMetadata,
    GeneratePlanMetadata,
    ModifyTodoMetadata,
    ReadFileMetadata,
    Tool,
    ToolExecutionResult,
    WriteFileMetadata,
)


MAX_DELEGATED_QUERIES = 50
TODAY = datetime.now().strftime("%d %B %Y")


class ReadFileArgs(BaseModel):
    path: str


class WriteFileArgs(BaseModel):
    path: str
    contents: str


class EditFileArgs(BaseModel):
    path: str
    old_text: str = Field(
        ...,
        min_length=1,
        description="The exact text to replace.",
    )
    new_text: str = Field(
        ...,
        description="The replacement text.",
    )


async def read_file(
    args: ReadFileArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    path = Path(args.path)
    if not path.exists() or not path.is_file():
        return ToolExecutionResult(
            model_response={"error": f"File does not exist: {args.path}"}
        )

    contents = path.read_text(encoding="utf-8")

    return ToolExecutionResult(
        model_response={
            "result": f"""
Read file at path {args.path}

<content>
{contents}
</content>""".strip()
        },
        metadata=ReadFileMetadata(
            path=args.path,
            contents=contents,
        ),
    )


async def write_file(
    args: WriteFileArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    path = Path(args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.contents, encoding="utf-8")

    return ToolExecutionResult(
        model_response={
            "result": f"Wrote file at path {args.path}",
            "path": args.path,
        },
        metadata=WriteFileMetadata(
            path=args.path,
            contents=args.contents,
        ),
    )


async def edit_file(
    args: EditFileArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    path = Path(args.path)
    if not path.exists() or not path.is_file():
        return ToolExecutionResult(
            model_response={"error": f"File does not exist: {args.path}"}
        )

    contents = path.read_text(encoding="utf-8")
    if args.old_text not in contents:
        return ToolExecutionResult(
            model_response={
                "error": (
                    f"Could not find the requested text to replace in {args.path}"
                )
            }
        )

    updated_contents = contents.replace(args.old_text, args.new_text, 1)
    path.write_text(updated_contents, encoding="utf-8")

    return ToolExecutionResult(
        model_response={
            "result": f"Edited file at path {args.path}",
            "path": args.path,
        },
        metadata=EditFileMetadata(
            path=args.path,
            old_text=args.old_text,
            new_text=args.new_text,
        ),
    )


class ModifyTodoArgs(BaseModel):
    action: Literal["add", "remove"]
    todos: list[str]


async def modify_todo(
    args: ModifyTodoArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    if args.action == "add":
        state.add_todos(args.todos)
        return ToolExecutionResult(
            model_response={
                "result": f"""
Todos updated to

<todos>
{chr(10).join(state.todos)}
</todos>""".strip()
            },
            metadata=ModifyTodoMetadata(action=args.action, todos=list(args.todos)),
        )

    requested = [todo.strip() for todo in args.todos]
    missing = []
    existing_lower = {todo.lower() for todo in state.todos}
    for todo in requested:
        if todo.lower() not in existing_lower:
            missing.append(todo)

    if missing:
        return ToolExecutionResult(
            model_response={"error": f"Todos not found: {', '.join(missing)}"}
        )

    state.remove_todos(args.todos)
    return ToolExecutionResult(
        model_response={
            "result": f"""
Todos updated to

<todos>
{chr(10).join(state.todos)}
</todos>""".strip()
        },
        metadata=ModifyTodoMetadata(action=args.action, todos=list(args.todos)),
    )


class BashArgs(BaseModel):
    command: str = Field(
        ...,
        min_length=1,
        description="A bash command to run in the current working directory.",
    )


async def bash(
    args: BashArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    try:
        process = await asyncio.create_subprocess_shell(
            args.command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=30,
        )
    except TimeoutError:
        return ToolExecutionResult(
            model_response={
                "error": f"Command timed out after 30 seconds: {args.command}"
            }
        )

    stdout = stdout_bytes.decode("utf-8", errors="replace").strip()
    stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
    returncode = process.returncode or 0

    return ToolExecutionResult(
        model_response={
            "result": f"""
Executed the following command:
{args.command}

Exit code: {returncode}

<result>
{stdout}
</result>

<stderr>
{stderr}
</stderr>""".strip(),
        },
        metadata=BashMetadata(
            command=args.command,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        ),
    )


class GeneratePlanArgs(BaseModel):
    todos: list[str] = Field(
        ...,
        min_length=1,
        description=(
            "Call this when you have enough information from the user. "
            "Provide the initial list of todos needed to execute the task."
        ),
    )

    @field_validator("todos")
    @classmethod
    def validate_todos(cls, todos: list[str]) -> list[str]:
        normalized_todos: list[str] = []
        seen: set[str] = set()
        for todo in todos:
            normalized = " ".join(todo.split())
            if not normalized:
                raise ValueError("Todos must not be empty.")
            normalized_key = normalized.lower()
            if normalized_key in seen:
                raise ValueError("Todos must be distinct.")
            seen.add(normalized_key)
            normalized_todos.append(normalized)
        return normalized_todos


async def generate_plan(
    args: GeneratePlanArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del context
    added = state.add_todos(args.todos)
    state.mode = "execute"
    return ToolExecutionResult(
        model_response={
            "result": "Plan accepted. Start executing the task.",
            "todos": list(state.todos),
            "mode": state.mode,
        },
        metadata=GeneratePlanMetadata(todos=added),
    )


READ_FILE_TOOL = Tool(
    name="read_file",
    description="Read a UTF-8 text file and return its contents.",
    args_model=ReadFileArgs,
    handler=read_file,
)

WRITE_FILE_TOOL = Tool(
    name="write_file",
    description="Write a UTF-8 text file to disk.",
    args_model=WriteFileArgs,
    handler=write_file,
)

EDIT_FILE_TOOL = Tool(
    name="edit_file",
    description="Replace an exact text snippet in a UTF-8 text file.",
    args_model=EditFileArgs,
    handler=edit_file,
)

MODIFY_TODO_TOOL = Tool(
    name="modify_todo",
    description="Add or remove todos from the current run state.",
    args_model=ModifyTodoArgs,
    handler=modify_todo,
)


BASH_TOOL = Tool(
    name="bash",
    description="Run a bash command in the current working directory and capture stdout and stderr.",
    args_model=BashArgs,
    handler=bash,
)

GENERATE_PLAN_TOOL = Tool(
    name="generate_plan",
    description="Call this when you have enough information from the user.",
    args_model=GeneratePlanArgs,
    handler=generate_plan,
)
