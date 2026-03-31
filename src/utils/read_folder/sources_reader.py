"""Utilities for discovering and reading files under data/sources/.

Directory layout assumed
------------------------
    <project_root>/
        data/
            sources/
                pdfs/            ← original PDF files
                pdfs_converted/  ← converted outputs (.mmd, .md, .html, …)

Filename convention for PDFs
-----------------------------
    <Title> - <Author>, <Publisher>, <Year>.pdf
    e.g. "All of Statistics - Larry Wasserman, Springer, 2004.pdf"
"""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

# src/utils/read_folder/ → src/utils/ → src/ → project_root
_PROJECT_ROOT = Path(__file__).parent.parent.parent.parent

SOURCES_DIR = _PROJECT_ROOT / "data" / "sources"
PDFS_DIR = SOURCES_DIR / "pdfs"
CONVERTED_DIR = SOURCES_DIR / "pdfs_converted"

# Regex that matches the standard PDF naming convention
_FILENAME_RE = re.compile(
    r"^(?P<title>.+?)\s+-\s+(?P<author>.+?),\s*(?P<publisher>.+?),\s*(?P<year>\d{4})$"
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class SourceFile:
    """Represents a single file under data/sources/."""

    path: Path
    stem: str = field(init=False)
    suffix: str = field(init=False)

    def __post_init__(self) -> None:
        self.stem = self.path.stem
        self.suffix = self.path.suffix.lower()

    @property
    def name(self) -> str:
        return self.path.name

    def read_text(self, encoding: str = "utf-8") -> str:
        """Read and return the file contents as a string."""
        return self.path.read_text(encoding=encoding)

    def __repr__(self) -> str:
        return f"SourceFile({self.path.relative_to(_PROJECT_ROOT)})"


@dataclass
class ParsedPdf:
    """Metadata extracted from a PDF filename."""

    path: Path
    title: str
    author: str
    publisher: str
    year: str

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def stem(self) -> str:
        return self.path.stem

    def __repr__(self) -> str:
        return f"ParsedPdf({self.title!r}, {self.year})"


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def get_sources_dir() -> Path:
    """Return the absolute path to data/sources/."""
    return SOURCES_DIR


def get_pdfs_dir() -> Path:
    """Return the absolute path to data/sources/pdfs/."""
    return PDFS_DIR


def get_converted_dir() -> Path:
    """Return the absolute path to data/sources/pdfs_converted/."""
    return CONVERTED_DIR


# ---------------------------------------------------------------------------
# Listing functions
# ---------------------------------------------------------------------------


def list_pdfs() -> list[SourceFile]:
    """Return all PDF files in data/sources/pdfs/, sorted by name."""
    if not PDFS_DIR.exists():
        return []
    return sorted(
        [SourceFile(p) for p in PDFS_DIR.iterdir() if p.suffix.lower() == ".pdf"],
        key=lambda f: f.name,
    )


def list_converted_files(ext: str | None = None) -> list[SourceFile]:
    """Return files in data/sources/pdfs_converted/.

    Parameters
    ----------
    ext:
        Optional extension filter, e.g. ``".mmd"``, ``".md"``, ``".html"``.
        Include the leading dot. If *None*, all files are returned.
    """
    if not CONVERTED_DIR.exists():
        return []
    files = [p for p in CONVERTED_DIR.iterdir() if p.is_file()]
    if ext is not None:
        ext_lower = ext.lower()
        files = [p for p in files if p.suffix.lower() == ext_lower]
    return sorted([SourceFile(p) for p in files], key=lambda f: f.name)


def iter_unprocessed_pdfs(ext: str = ".mmd") -> Iterator[SourceFile]:
    """Yield PDF files that have no corresponding converted output.

    A PDF is considered processed when a converted file with the same stem
    (and the given *ext*) exists in data/sources/pdfs_converted/.

    Parameters
    ----------
    ext:
        Extension of the expected converted file, default ``".mmd"``.
    """
    converted_stems = {f.stem for f in list_converted_files(ext=ext)}
    for pdf in list_pdfs():
        if pdf.stem not in converted_stems:
            yield pdf


# ---------------------------------------------------------------------------
# Filename parsing
# ---------------------------------------------------------------------------


def parse_filename(filename: str) -> ParsedPdf | None:
    """Parse a standardised PDF filename and return a :class:`ParsedPdf`.

    Expected format::

        <Title> - <Author>, <Publisher>, <Year>.pdf

    Returns *None* if the filename does not match the convention.
    """
    stem = Path(filename).stem
    match = _FILENAME_RE.match(stem)
    if match is None:
        return None
    path = PDFS_DIR / filename if not Path(filename).is_absolute() else Path(filename)
    return ParsedPdf(
        path=path,
        title=match.group("title").strip(),
        author=match.group("author").strip(),
        publisher=match.group("publisher").strip(),
        year=match.group("year").strip(),
    )


def parse_all_pdfs() -> list[ParsedPdf]:
    """Parse filenames of every PDF in data/sources/pdfs/.

    Files whose names do not match the convention are silently skipped.
    """
    results: list[ParsedPdf] = []
    for sf in list_pdfs():
        parsed = parse_filename(sf.name)
        if parsed is not None:
            results.append(parsed)
    return results


# ---------------------------------------------------------------------------
# Reading converted files
# ---------------------------------------------------------------------------


def read_converted_file(path: str | Path, encoding: str = "utf-8") -> str:
    """Read a converted file and return its text content.

    Parameters
    ----------
    path:
        Absolute path, or a bare filename resolved relative to
        ``data/sources/pdfs_converted/``.
    encoding:
        Text encoding, default ``"utf-8"``.
    """
    resolved = Path(path)
    if not resolved.is_absolute():
        resolved = CONVERTED_DIR / resolved
    return resolved.read_text(encoding=encoding)


def find_converted_for_pdf(
    pdf_name: str, ext: str = ".mmd"
) -> SourceFile | None:
    """Return the converted :class:`SourceFile` for a given PDF filename.

    Parameters
    ----------
    pdf_name:
        PDF filename (with or without ``.pdf`` suffix).
    ext:
        Extension of the converted output to look for, default ``".mmd"``.
    """
    stem = Path(pdf_name).stem
    target = CONVERTED_DIR / f"{stem}{ext}"
    if target.exists():
        return SourceFile(target)
    return None
