"""Mathpix PDF conversion tool for the Library Boss agent.

Exposes two tools:
  - convert_pdf_file  — upload a local PDF and return Mathpix Markdown text
  - convert_pdf_url   — submit a remote PDF URL and return Mathpix Markdown text
"""

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, Field

from agent_generic.state import AgentContext, RunState
from tools.abstract import ConvertPdfMetadata, Tool, ToolExecutionResult
from utils.mathpix_conversion.mathpix_pdf_converter import (
    _FORMAT_EXT,
    convert_pdf,
    download_result,
    submit_pdf_url,
    wait_for_completion,
)


# ---------------------------------------------------------------------------
# Args models
# ---------------------------------------------------------------------------

class ConvertPdfFileArgs(BaseModel):
    file_path: str = Field(
        ...,
        description=(
            "Absolute or relative path to the local PDF file to convert. "
            "Supports PDF, EPUB, DOCX, PPTX and other formats accepted by Mathpix."
        ),
    )
    output_formats: list[str] = Field(
        default=["mmd"],
        description=(
            "List of output formats to request. Supported values: "
            "'mmd' (Mathpix Markdown), 'md', 'docx', 'tex.zip', 'html', 'pdf', 'lines.json'. "
            "Defaults to ['mmd']."
        ),
    )
    output_dir: str = Field(
        default="",
        description=(
            "Directory to save converted files to. "
            "Leave empty to return content in memory only."
        ),
    )


class ConvertPdfUrlArgs(BaseModel):
    url: str = Field(
        ...,
        description="Publicly accessible HTTP(S) URL of the PDF (or EPUB, DOCX, …) to convert.",
    )
    output_formats: list[str] = Field(
        default=["mmd"],
        description=(
            "List of output formats to request. Supported values: "
            "'mmd' (Mathpix Markdown), 'md', 'docx', 'tex.zip', 'html', 'pdf', 'lines.json'. "
            "Defaults to ['mmd']."
        ),
    )
    output_dir: str = Field(
        default="",
        description=(
            "Directory to save converted files to. "
            "Leave empty to return content in memory only."
        ),
    )


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def _build_result(
    result: dict,
    source: str,
    output_formats: list[str],
) -> ToolExecutionResult:
    """Shared response builder for both file and URL conversion handlers."""
    pdf_id = result["pdf_id"]
    num_pages = result["status"].get("num_pages")
    mmd_text: str = result.get("mmd_text", "")

    saved_paths = {
        fmt: str(result[f"{fmt}_path"])
        for fmt in output_formats
        if result.get(f"{fmt}_path") is not None
    }

    response: dict = {
        "pdf_id": pdf_id,
        "source": source,
        "num_pages": num_pages,
        "output_formats": output_formats,
        "saved_paths": saved_paths,
    }

    if mmd_text:
        response["mmd_preview"] = mmd_text[:1000] + ("…" if len(mmd_text) > 1000 else "")
        response["mmd_text"] = mmd_text
        response["result"] = (
            f"Converted '{source}' ({num_pages} pages, pdf_id={pdf_id}).\n"
            f"MMD output ({len(mmd_text)} chars):\n\n{mmd_text[:2000]}"
            + ("…" if len(mmd_text) > 2000 else "")
        )
    else:
        response["result"] = (
            f"Converted '{source}' ({num_pages} pages, pdf_id={pdf_id}). "
            f"Formats saved: {', '.join(saved_paths) or 'none (in-memory only)'}."
        )

    return ToolExecutionResult(
        model_response=response,
        metadata=ConvertPdfMetadata(
            source=source,
            pdf_id=pdf_id,
            num_pages=num_pages,
            output_formats=output_formats,
            mmd_chars=len(mmd_text),
        ),
    )


async def convert_pdf_file_handler(
    args: ConvertPdfFileArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    output_dir = Path(args.output_dir) if args.output_dir else None

    try:
        result = convert_pdf(
            args.file_path,
            output_formats=args.output_formats,
            output_dir=output_dir,
        )
    except FileNotFoundError:
        return ToolExecutionResult(
            model_response={"error": f"File not found: {args.file_path}"},
        )
    except (RuntimeError, TimeoutError) as exc:
        return ToolExecutionResult(
            model_response={"error": str(exc)},
        )

    return _build_result(result, source=args.file_path, output_formats=args.output_formats)


async def convert_pdf_url_handler(
    args: ConvertPdfUrlArgs,
    state: RunState,
    context: AgentContext,
) -> ToolExecutionResult:
    del state, context

    output_dir = Path(args.output_dir) if args.output_dir else None
    conversion_formats = {
        fmt: True for fmt in args.output_formats if fmt != "mmd"
    } or None

    try:
        pdf_id = submit_pdf_url(args.url, conversion_formats=conversion_formats)
        final_status = wait_for_completion(pdf_id)

        downloaded: dict = {"pdf_id": pdf_id, "status": final_status}
        for fmt in args.output_formats:
            raw = download_result(pdf_id, fmt)
            downloaded[f"{fmt}_bytes"] = raw
            if fmt in ("mmd", "md", "html", "lines.json"):
                downloaded[f"{fmt}_text"] = raw.decode("utf-8")
            if output_dir is not None:
                out = output_dir / f"result{_FORMAT_EXT[fmt]}"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(raw)
                downloaded[f"{fmt}_path"] = out
            else:
                downloaded[f"{fmt}_path"] = None

    except (RuntimeError, TimeoutError) as exc:
        return ToolExecutionResult(
            model_response={"error": str(exc)},
        )

    return _build_result(downloaded, source=args.url, output_formats=args.output_formats)


# ---------------------------------------------------------------------------
# Tool instances
# ---------------------------------------------------------------------------

CONVERT_PDF_FILE_TOOL = Tool(
    name="convert_pdf_file",
    description=(
        "Upload a local PDF (or EPUB, DOCX, PPTX) file to Mathpix for OCR conversion. "
        "Returns the full Mathpix Markdown text and optionally saves files to disk. "
        "Use this when the file is available locally."
    ),
    args_model=ConvertPdfFileArgs,
    handler=convert_pdf_file_handler,
)

CONVERT_PDF_URL_TOOL = Tool(
    name="convert_pdf_url",
    description=(
        "Submit a publicly accessible PDF (or EPUB, DOCX, PPTX) URL to Mathpix for OCR conversion. "
        "Returns the full Mathpix Markdown text and optionally saves files to disk. "
        "Use this when the document is available at a URL."
    ),
    args_model=ConvertPdfUrlArgs,
    handler=convert_pdf_url_handler,
)
