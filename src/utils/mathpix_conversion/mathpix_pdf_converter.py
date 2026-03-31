"""Mathpix PDF converter — authenticate and convert PDF files via the Mathpix v3 API.

Credentials are read from the environment:
    MATHPIX_APP_ID  — your Mathpix app_id
    MATHPIX_APP_KEY — your Mathpix app_key

Typical usage
-------------
    result = convert_pdf("path/to/document.pdf", output_formats=["mmd", "docx"])
    print(result["mmd_text"])          # Mathpix Markdown string
    result["mmd_path"]                 # Path to saved .mmd file (if output_dir given)
"""

import os
import time
from pathlib import Path

import requests
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(raise_error_if_not_found=False))

_API_BASE = "https://api.mathpix.com/v3"

# Supported download extensions and their MIME types
_FORMAT_EXT: dict[str, str] = {
    "mmd": ".mmd",
    "md": ".md",
    "docx": ".docx",
    "tex.zip": ".tex.zip",
    "html": ".html",
    "pdf": ".pdf",
    "lines.json": ".lines.json",
}


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def get_auth_headers() -> dict[str, str]:
    """Return the request headers required by every Mathpix API call.

    Reads MATHPIX_APP_ID and MATHPIX_APP_KEY from the environment.

    Raises:
        RuntimeError: if either credential is missing.
    """
    app_id = os.getenv("MATHPIX_APP_ID")
    app_key = os.getenv("MATHPIX_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError(
            "MATHPIX_APP_ID and MATHPIX_APP_KEY must be set in the environment."
        )
    return {
        "app_id": app_id,
        "app_key": app_key,
    }


def verify_credentials() -> bool:
    """Check that the stored credentials are accepted by the Mathpix API.

    Sends a lightweight authenticated GET to the status endpoint.

    Returns:
        True if the server returns a non-401/403 response.

    Raises:
        RuntimeError: if credentials are missing.
    """
    headers = get_auth_headers()
    # Use the PDF status endpoint with a dummy id — a 404 means auth passed.
    response = requests.get(
        f"{_API_BASE}/pdf/credential_check",
        headers=headers,
        timeout=10,
    )
    if response.status_code in (401, 403):
        return False
    return True


# ---------------------------------------------------------------------------
# Submit
# ---------------------------------------------------------------------------

def submit_pdf_file(
    file_path: str | Path,
    *,
    conversion_formats: dict[str, bool] | None = None,
    options: dict | None = None,
) -> str:
    """Upload a local PDF (or EPUB, DOCX, …) for async OCR processing.

    Args:
        file_path: Path to the local file to upload.
        conversion_formats: Optional dict of output formats to enable, e.g.
            ``{"docx": True, "tex.zip": True}``.  When omitted only the base
            Mathpix Markdown (.mmd) is produced.
        options: Any extra POST body parameters supported by the v3/pdf endpoint
            (e.g. ``{"rm_spaces": True, "enable_tables_fallback": True}``).

    Returns:
        The ``pdf_id`` string assigned by Mathpix.

    Raises:
        requests.HTTPError: on a non-2xx response.
        RuntimeError: if credentials are missing.
    """
    file_path = Path(file_path)
    headers = get_auth_headers()

    body: dict = {}
    if conversion_formats:
        body["conversion_formats"] = conversion_formats
    if options:
        body.update(options)

    with file_path.open("rb") as fh:
        response = requests.post(
            f"{_API_BASE}/pdf",
            headers=headers,
            files={"file": (file_path.name, fh, "application/pdf")},
            data={"options_json": _json_dumps(body)} if body else {},
            timeout=120,
        )

    response.raise_for_status()
    return response.json()["pdf_id"]


def submit_pdf_url(
    url: str,
    *,
    conversion_formats: dict[str, bool] | None = None,
    options: dict | None = None,
) -> str:
    """Submit a publicly accessible document URL for async OCR processing.

    Args:
        url: HTTP(S) URL of the document.
        conversion_formats: Optional output formats, e.g. ``{"docx": True}``.
        options: Any extra v3/pdf POST body parameters.

    Returns:
        The ``pdf_id`` string assigned by Mathpix.

    Raises:
        requests.HTTPError: on a non-2xx response.
        RuntimeError: if credentials are missing.
    """
    headers = {**get_auth_headers(), "Content-Type": "application/json"}

    body: dict = {"url": url}
    if conversion_formats:
        body["conversion_formats"] = conversion_formats
    if options:
        body.update(options)

    response = requests.post(
        f"{_API_BASE}/pdf",
        headers=headers,
        json=body,
        timeout=30,
    )
    response.raise_for_status()
    return response.json()["pdf_id"]


# ---------------------------------------------------------------------------
# Status polling
# ---------------------------------------------------------------------------

def get_pdf_status(pdf_id: str) -> dict:
    """Return the current processing status dict for a submitted PDF.

    Relevant fields in the response:
        - ``status``: one of ``received | loaded | split | completed | error``
        - ``num_pages``, ``num_pages_completed``, ``percent_done``

    Raises:
        requests.HTTPError: on a non-2xx response.
        RuntimeError: if credentials are missing.
    """
    response = requests.get(
        f"{_API_BASE}/pdf/{pdf_id}",
        headers=get_auth_headers(),
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def wait_for_completion(
    pdf_id: str,
    *,
    poll_interval: float = 3.0,
    timeout: float = 600.0,
) -> dict:
    """Poll until the PDF reaches ``completed`` or ``error`` status.

    Args:
        pdf_id: The tracking ID returned by :func:`submit_pdf_file` or
            :func:`submit_pdf_url`.
        poll_interval: Seconds between status checks (default 3 s).
        timeout: Maximum total wait time in seconds (default 600 s).

    Returns:
        The final status dict from the API.

    Raises:
        TimeoutError: if ``timeout`` is exceeded before completion.
        RuntimeError: if the API reports ``status == "error"``.
        requests.HTTPError: on a non-2xx response.
    """
    deadline = time.monotonic() + timeout
    while True:
        status = get_pdf_status(pdf_id)
        state = status.get("status", "")
        if state == "completed":
            return status
        if state == "error":
            raise RuntimeError(
                f"Mathpix processing failed for pdf_id={pdf_id!r}: {status}"
            )
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"Mathpix processing timed out after {timeout}s "
                f"(pdf_id={pdf_id!r}, last status={state!r})"
            )
        time.sleep(poll_interval)


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_result(
    pdf_id: str,
    fmt: str,
    *,
    output_path: str | Path | None = None,
) -> bytes:
    """Download a completed conversion result.

    Args:
        pdf_id: The Mathpix tracking ID.
        fmt: Format extension without leading dot — one of
            ``mmd``, ``md``, ``docx``, ``tex.zip``, ``html``, ``pdf``,
            ``lines.json``.
        output_path: If given, the content is also written to this path.

    Returns:
        Raw bytes of the downloaded file.

    Raises:
        ValueError: if ``fmt`` is not a recognised extension.
        requests.HTTPError: on a non-2xx response.
        RuntimeError: if credentials are missing.
    """
    if fmt not in _FORMAT_EXT:
        raise ValueError(
            f"Unknown format {fmt!r}. "
            f"Choose from: {', '.join(_FORMAT_EXT)}"
        )

    ext = _FORMAT_EXT[fmt]
    response = requests.get(
        f"{_API_BASE}/pdf/{pdf_id}{ext}",
        headers=get_auth_headers(),
        timeout=120,
    )
    response.raise_for_status()
    content = response.content

    if output_path is not None:
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(content)

    return content


# ---------------------------------------------------------------------------
# High-level convenience function
# ---------------------------------------------------------------------------

def convert_pdf(
    file_path: str | Path,
    *,
    output_formats: list[str] | None = None,
    output_dir: str | Path | None = None,
    poll_interval: float = 3.0,
    timeout: float = 600.0,
    options: dict | None = None,
) -> dict:
    """Upload a PDF, wait for processing, and download all requested formats.

    Args:
        file_path: Path to the local PDF (or EPUB, DOCX, …).
        output_formats: List of format keys to download after processing.
            Defaults to ``["mmd"]``.  Supported values: ``mmd``, ``md``,
            ``docx``, ``tex.zip``, ``html``, ``pdf``, ``lines.json``.
        output_dir: Directory to save downloaded files.  When omitted files
            are not saved to disk; content is returned in memory only.
        poll_interval: Seconds between status poll requests.
        timeout: Maximum seconds to wait for completion.
        options: Extra v3/pdf POST body parameters.

    Returns:
        A dict with:
            - ``pdf_id`` (str) — Mathpix tracking ID
            - ``status`` (dict) — final status response
            - One key per requested format:
                - ``"{fmt}_bytes"`` (bytes) — raw file content
                - ``"{fmt}_text"`` (str) — decoded text (for text formats)
                - ``"{fmt}_path"`` (Path | None) — saved path if output_dir given

    Raises:
        RuntimeError: if credentials are missing or processing failed.
        TimeoutError: if processing exceeds ``timeout``.
        requests.HTTPError: on any non-2xx API response.
    """
    file_path = Path(file_path)
    output_formats = output_formats or ["mmd"]

    # Build conversion_formats dict for non-mmd outputs (mmd is always produced)
    conversion_formats = {
        fmt: True for fmt in output_formats if fmt != "mmd"
    } or None

    pdf_id = submit_pdf_file(
        file_path,
        conversion_formats=conversion_formats,
        options=options,
    )

    final_status = wait_for_completion(
        pdf_id,
        poll_interval=poll_interval,
        timeout=timeout,
    )

    result: dict = {"pdf_id": pdf_id, "status": final_status}

    stem = file_path.stem
    for fmt in output_formats:
        raw = download_result(pdf_id, fmt)
        result[f"{fmt}_bytes"] = raw

        # Decode text-based formats
        if fmt in ("mmd", "md", "html", "lines.json"):
            result[f"{fmt}_text"] = raw.decode("utf-8")

        # Optionally save to disk
        if output_dir is not None:
            out = Path(output_dir) / f"{stem}{_FORMAT_EXT[fmt]}"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(raw)
            result[f"{fmt}_path"] = out
        else:
            result[f"{fmt}_path"] = None

    return result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _json_dumps(obj: dict) -> str:
    import json
    return json.dumps(obj)
