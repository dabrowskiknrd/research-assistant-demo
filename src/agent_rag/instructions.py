"""System prompt helpers for the Research Assistant RAG agent."""


def build_rag_prompt(question: str, books: list[str] | None) -> str:
    """Build the full prompt for a RAG query.

    When a ``books`` list is provided the model is explicitly instructed to
    draw its answer *only* from those sources, mirroring the scoping behaviour
    of the Gemini FileSearch store query used in ``app.py``.
    """
    if books:
        book_lines = "\n".join(f"  - {b}" for b in books)
        scope = (
            "You have access to a document store.\n"
            "Answer the question using ONLY the following books:\n"
            f"{book_lines}\n\n"
            "Do not draw on any other documents that may be present in the store."
        )
    else:
        scope = (
            "You have access to a document store. "
            "Answer the question using all available documents."
        )

    return f"{scope}\n\nQuestion: {question}"
