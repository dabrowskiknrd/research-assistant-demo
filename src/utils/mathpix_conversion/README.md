# Utils

Utility modules shared across the project.

---

## `mathpix_pdf_converter.py`

Converts PDF files (and EPUB, DOCX, PPTX, …) to Mathpix Markdown and other formats via the [Mathpix v3 API](https://docs.mathpix.com/guides/pdf-processing).

### Prerequisites

Add your credentials to `.env` at the project root:

```env
MATHPIX_APP_ID=your_app_id
MATHPIX_APP_KEY=your_app_key
```

Get keys from [console.mathpix.com](https://console.mathpix.com/).

### Functions

| Function | Description |
|---|---|
| `get_auth_headers()` | Returns `{app_id, app_key}` headers loaded from ENV |
| `verify_credentials()` | Checks that credentials are accepted by the API |
| `submit_pdf_file(file_path, ...)` | Uploads a local file → returns `pdf_id` |
| `submit_pdf_url(url, ...)` | Submits a remote URL → returns `pdf_id` |
| `get_pdf_status(pdf_id)` | Returns the current processing status dict |
| `wait_for_completion(pdf_id, ...)` | Polls until `completed`; raises on error or timeout |
| `download_result(pdf_id, fmt, ...)` | Downloads one result format by extension |
| `convert_pdf(file_path, ...)` | **All-in-one**: upload → wait → download all formats |

### Supported output formats

| Key | Extension | Description |
|---|---|---|
| `mmd` | `.mmd` | Mathpix Markdown (always available) |
| `md` | `.md` | Standard Markdown |
| `docx` | `.docx` | Microsoft Word |
| `tex.zip` | `.tex.zip` | LaTeX archive |
| `html` | `.html` | HTML |
| `pdf` | `.pdf` | Rendered PDF |
| `lines.json` | `.lines.json` | Structured line-by-line JSON |

### Quick start

```python
from utils.mathpix_pdf_converter import convert_pdf

result = convert_pdf(
    "my_paper.pdf",
    output_formats=["mmd", "docx"],
    output_dir="output/",
)

print(result["mmd_text"])    # Mathpix Markdown string
# result["mmd_path"]         # Path("output/my_paper.mmd")
# result["docx_path"]        # Path("output/my_paper.docx")
```

See [convert_pdf_example.ipynb](convert_pdf_example.ipynb) for a step-by-step walkthrough.
