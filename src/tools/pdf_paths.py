"""Path/file-system tools for the Library Boss agent.

Exposes four tools backed by utils.read_folder.sources_reader:
  - list_source_pdfs        — list PDFs in data/sources/pdfs/ with parsed metadata
  - list_converted_files    — list files in data/sources/pdfs_converted/
  - list_unprocessed_pdfs   — list PDFs that have no corresponding converted output
  - read_converted_file     — read the full text of a converted file
"""

from pydantic import BaseModel, Field

from agent_generic.state import AgentContext, RunState
from tools.abstract import (
    ListConvertedFilesMetadata,
    ListSourcePdfsMetadata,
    ListUnprocessedPdfsMetadata,
    ReadConvertedFileMetadata,
    Tool,
    ToolExecutionResult,
)
from utils.read_folder.sources_reader import (
    find_converted_for_pdf,
    iter_unprocessed_pdfs,
    list_converted_files,
    list_pdfs,
    parse_all_pdfs,
    read_converted_file,
)


# ---------------------------------------------------------------------------
# list_source_pdfs
# ---------------------------------------------------------------------------


async def list_source_pdfs_handler(
    args: "ListSourcePdfsArgs",
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    all_pdfs = list_pdfs()
    parsed_pdfs = {p.stem: p for p in parse_all_pdfs()}

    entries: list[dict] = []
    for sf in all_pdfs:
        if sf.stem in parsed_pdfs:
            p = parsed_pdfs[sf.stem]
            entries.append(
                {
                    "filename": sf.name,
                    "title": p.title,
                    "author": p.author,
                    "publisher": p.publisher,
                    "year": p.year,
                }
            )
        else:
            entries.append({"filename": sf.name})

    return ToolExecutionResult(
        model_response={
            "total": len(entries),
            "parsed": len(parsed_pdfs),
            "pdfs": entries,
        },
        metadata=ListSourcePdfsMetadata(total=len(entries), parsed=len(parsed_pdfs)),
    )


class ListSourcePdfsArgs(BaseModel):
    pass


LIST_SOURCE_PDFS_TOOL = Tool(
    name="list_source_pdfs",
    description=(
        "List all PDF files available in the local data/sources/pdfs/ folder. "
        "Returns filename, title, author, publisher and year for files that follow "
        "the standard naming convention."
    ),
    args_model=ListSourcePdfsArgs,
    handler=list_source_pdfs_handler,
)


# ---------------------------------------------------------------------------
# list_converted_files
# ---------------------------------------------------------------------------


class ListConvertedFilesArgs(BaseModel):
    ext: str = Field(
        default="",
        description=(
            "Optional extension filter including the leading dot, e.g. '.mmd', '.md', '.html'. "
            "Leave empty to list all converted files regardless of extension."
        ),
    )


async def list_converted_files_handler(
    args: ListConvertedFilesArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    ext_filter = args.ext.strip() or None
    files = list_converted_files(ext=ext_filter)

    entries = [{"filename": f.name, "suffix": f.suffix} for f in files]

    return ToolExecutionResult(
        model_response={
            "total": len(entries),
            "ext_filter": ext_filter,
            "files": entries,
        },
        metadata=ListConvertedFilesMetadata(total=len(entries), ext_filter=ext_filter),
    )


LIST_CONVERTED_FILES_TOOL = Tool(
    name="list_converted_files",
    description=(
        "List files in data/sources/pdfs_converted/. "
        "Optionally filter by extension (e.g. '.mmd', '.md'). "
        "Use this to see which PDFs have already been converted."
    ),
    args_model=ListConvertedFilesArgs,
    handler=list_converted_files_handler,
)


# ---------------------------------------------------------------------------
# list_unprocessed_pdfs
# ---------------------------------------------------------------------------


class ListUnprocessedPdfsArgs(BaseModel):
    ext: str = Field(
        default=".mmd",
        description=(
            "Extension of the expected converted output used to decide whether a "
            "PDF has been processed. Defaults to '.mmd'."
        ),
    )


async def list_unprocessed_pdfs_handler(
    args: ListUnprocessedPdfsArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    unprocessed = list(iter_unprocessed_pdfs(ext=args.ext))
    entries = [{"filename": f.name} for f in unprocessed]

    return ToolExecutionResult(
        model_response={
            "total": len(entries),
            "ext": args.ext,
            "pdfs": entries,
        },
        metadata=ListUnprocessedPdfsMetadata(total=len(entries), ext=args.ext),
    )


LIST_UNPROCESSED_PDFS_TOOL = Tool(
    name="list_unprocessed_pdfs",
    description=(
        "List PDF files in data/sources/pdfs/ that do not yet have a corresponding "
        "converted output file. Use this to identify PDFs that still need conversion."
    ),
    args_model=ListUnprocessedPdfsArgs,
    handler=list_unprocessed_pdfs_handler,
)


# ---------------------------------------------------------------------------
# read_converted_file
# ---------------------------------------------------------------------------


class ReadConvertedFileArgs(BaseModel):
    filename: str = Field(
        ...,
        description=(
            "Filename of the converted file to read, e.g. "
            "'All of Statistics - Larry Wasserman, Springer, 2004.mmd'. "
            "A bare filename is resolved relative to data/sources/pdfs_converted/. "
            "Absolute paths are also accepted."
        ),
    )
    pdf_name: str = Field(
        default="",
        description=(
            "Optional: original PDF filename (with or without .pdf suffix). "
            "If provided together with ext, the tool looks up the converted file "
            "automatically — filename is not required in that case."
        ),
    )
    ext: str = Field(
        default=".mmd",
        description=(
            "Extension used when looking up via pdf_name. Defaults to '.mmd'."
        ),
    )


async def read_converted_file_handler(
    args: ReadConvertedFileArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    # Resolve filename: prefer explicit filename, fall back to pdf_name lookup
    target_filename = args.filename.strip()
    if not target_filename and args.pdf_name.strip():
        sf = find_converted_for_pdf(args.pdf_name.strip(), ext=args.ext)
        if sf is None:
            return ToolExecutionResult(
                model_response={
                    "error": (
                        f"No converted file found for PDF '{args.pdf_name}' "
                        f"with extension '{args.ext}'."
                    )
                }
            )
        target_filename = sf.name

    if not target_filename:
        return ToolExecutionResult(
            model_response={"error": "Provide either 'filename' or 'pdf_name'."}
        )

    try:
        text = read_converted_file(target_filename)
    except FileNotFoundError:
        return ToolExecutionResult(
            model_response={"error": f"File not found: '{target_filename}'."}
        )
    except Exception as exc:
        return ToolExecutionResult(
            model_response={"error": f"Failed to read file: {exc}"}
        )

    return ToolExecutionResult(
        model_response={
            "filename": target_filename,
            "char_count": len(text),
            "content": text,
        },
        metadata=ReadConvertedFileMetadata(
            filename=target_filename,
            char_count=len(text),
        ),
    )


READ_CONVERTED_FILE_TOOL = Tool(
    name="read_converted_file",
    description=(
        "Read the full text content of a converted source file from "
        "data/sources/pdfs_converted/. "
        "Pass either 'filename' (e.g. 'Book Title - Author, Publisher, Year.mmd') "
        "or 'pdf_name' + 'ext' to look up the converted file automatically."
    ),
    args_model=ReadConvertedFileArgs,
    handler=read_converted_file_handler,
)
