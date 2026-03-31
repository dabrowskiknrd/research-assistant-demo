import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

from agent_generic.state import AgentContext, RunState
from tools.abstract import SQLiteQueryMetadata, SQLiteWriteMetadata, Tool, ToolExecutionResult



_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SQLiteWriteArgs(BaseModel):
    db_path: str = Field(
        ...,
        description="Path to the SQLite database file.",
    )
    table: str = Field(
        ...,
        min_length=1,
        description="Name of the table to write to.",
    )
    data: dict[str, Any] = Field(
        ...,
        description="Mapping of column names to values to insert.",
    )
    mode: Literal["insert", "replace", "insert_or_ignore"] = Field(
        default="replace",
        description=(
            "Write mode: 'insert' (fail on conflict), "
            "'replace' (upsert via INSERT OR REPLACE), "
            "or 'insert_or_ignore' (skip on conflict)."
        ),
    )

    @field_validator("table")
    @classmethod
    def validate_table_name(cls, v: str) -> str:
        if not _IDENTIFIER_RE.match(v):
            raise ValueError(
                "Table name must start with a letter or underscore and contain "
                "only alphanumeric characters and underscores."
            )
        return v

    @field_validator("data")
    @classmethod
    def validate_column_names(cls, data: dict[str, Any]) -> dict[str, Any]:
        if not data:
            raise ValueError("data must contain at least one column-value pair.")
        for col in data:
            if not _IDENTIFIER_RE.match(col):
                raise ValueError(
                    f"Column name must start with a letter or underscore and contain "
                    f"only alphanumeric characters and underscores: {col!r}"
                )
        return data


class SQLiteQueryArgs(BaseModel):
    db_path: str = Field(
        ...,
        description="Path to the SQLite database file.",
    )
    query: str = Field(
        ...,
        min_length=1,
        description="A SELECT SQL query to execute. Use ? placeholders for parameters.",
    )
    params: list[Any] = Field(
        default_factory=list,
        description="Positional parameters bound to ? placeholders in the query.",
    )

    @field_validator("query")
    @classmethod
    def validate_select_only(cls, v: str) -> str:
        if not v.strip().upper().startswith("SELECT"):
            raise ValueError(
                "Only SELECT queries are permitted for sqlite_query. "
                "Use sqlite_write to modify data."
            )
        return v


async def sqlite_write(
    args: SQLiteWriteArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    db_path = Path(args.db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    columns = list(args.data.keys())
    values = [args.data[col] for col in columns]
    col_list = ", ".join(f'"{col}"' for col in columns)
    placeholders = ", ".join("?" for _ in columns)

    keyword = {
        "insert": "INSERT",
        "replace": "INSERT OR REPLACE",
        "insert_or_ignore": "INSERT OR IGNORE",
    }[args.mode]

    sql = f'{keyword} INTO "{args.table}" ({col_list}) VALUES ({placeholders})'

    try:
        conn = sqlite3.connect(args.db_path)
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.execute(sql, values)
            rows_affected = cursor.rowcount
            conn.commit()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return ToolExecutionResult(
            model_response={"error": f"SQLite error: {exc}"}
        )

    return ToolExecutionResult(
        model_response={
            "result": (
                f"Wrote {rows_affected} row(s) to table '{args.table}' "
                f"in {args.db_path}."
            ),
            "db_path": args.db_path,
            "table": args.table,
            "rows_affected": rows_affected,
        },
        metadata=SQLiteWriteMetadata(
            db_path=args.db_path,
            table=args.table,
            data=dict(args.data),
            rows_affected=rows_affected,
        ),
    )


async def sqlite_query(
    args: SQLiteQueryArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    db_path = Path(args.db_path)
    if not db_path.exists() or not db_path.is_file():
        return ToolExecutionResult(
            model_response={"error": f"Database does not exist: {args.db_path}"}
        )

    try:
        conn = sqlite3.connect(args.db_path)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            cursor = conn.execute(args.query, args.params)
            rows = [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return ToolExecutionResult(
            model_response={"error": f"SQLite error: {exc}"}
        )

    return ToolExecutionResult(
        model_response={
            "result": f"Query returned {len(rows)} row(s).",
            "rows": rows,
        },
        metadata=SQLiteQueryMetadata(
            db_path=args.db_path,
            query=args.query,
            row_count=len(rows),
        ),
    )


SQLITE_WRITE_TOOL = Tool(
    name="sqlite_write",
    description=(
        "Insert or upsert a single row into a SQLite database table. "
        "Use mode='replace' for upsert (INSERT OR REPLACE) and "
        "'insert_or_ignore' to skip duplicate rows silently."
    ),
    args_model=SQLiteWriteArgs,
    handler=sqlite_write,
)

SQLITE_QUERY_TOOL = Tool(
    name="sqlite_query",
    description=(
        "Execute a read-only SELECT query against a SQLite database and return "
        "the matching rows as a list of objects. Use ? placeholders and the "
        "params field to pass values safely."
    ),
    args_model=SQLiteQueryArgs,
    handler=sqlite_query,
)
