# read_folder

Utilities for discovering and reading files under `data/sources/`.

## Directory layout

```
<project_root>/
    data/
        sources/
            pdfs/            ← original PDF files
            pdfs_converted/  ← converted outputs (.mmd, .md, .html, …)
```

## PDF filename convention

PDFs are expected to follow this naming pattern:

```
<Title> - <Author>, <Publisher>, <Year>.pdf
```

Example:
```
All of Statistics - Larry Wasserman, Springer, 2004.pdf
```

---

## Usage

```python
import sys
sys.path.insert(0, "src")

from utils.read_folder.sources_reader import (
    list_pdfs,
    list_converted_files,
    iter_unprocessed_pdfs,
    parse_all_pdfs,
    parse_filename,
    read_converted_file,
    find_converted_for_pdf,
    get_pdfs_dir,
    get_converted_dir,
)
```

### List all PDFs

```python
for f in list_pdfs():
    print(f.name)       # filename
    print(f.path)       # absolute Path
    print(f.suffix)     # ".pdf"
```

### List converted files

```python
# All converted files
for f in list_converted_files():
    print(f.name)

# Only .mmd files
for f in list_converted_files(ext=".mmd"):
    print(f.name)
```

### Find PDFs not yet converted

```python
# Yields PDFs with no matching .mmd in pdfs_converted/
for pdf in iter_unprocessed_pdfs(ext=".mmd"):
    print(pdf.name)
```

### Parse PDF filenames into structured metadata

```python
# Parse a single filename
result = parse_filename("All of Statistics - Larry Wasserman, Springer, 2004.pdf")
print(result.title)      # "All of Statistics"
print(result.author)     # "Larry Wasserman"
print(result.publisher)  # "Springer"
print(result.year)       # "2004"

# Prase all PDFs in data/sources/pdfs/ at once
for book in parse_all_pdfs():
    print(book.title, book.year)
```

### Read a converted file

```python
# By absolute path
text = read_converted_file("/abs/path/to/file.mmd")

# By bare filename (resolved relative to pdfs_converted/)
text = read_converted_file("All of Statistics - Larry Wasserman, Springer, 2004.mmd")
print(text[:500])
```

### Find the converted file for a given PDF

```python
sf = find_converted_for_pdf(
    "All of Statistics - Larry Wasserman, Springer, 2004.pdf",
    ext=".mmd",
)
if sf:
    text = sf.read_text()
```

### Path helpers

```python
from utils.read_folder.sources_reader import get_pdfs_dir, get_converted_dir, get_sources_dir

print(get_sources_dir())    # .../data/sources
print(get_pdfs_dir())       # .../data/sources/pdfs
print(get_converted_dir())  # .../data/sources/pdfs_converted
```
