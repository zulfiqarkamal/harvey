"""
Gamma7 — Document Loader
Shared input layer for both Supplier Assurance and Audit Intelligence agents.

Supported formats:
  - Markdown (.md)
  - Plain text (.txt)
  - PDF (text-based, via pypdf; fallback to pdfminer for complex layouts)

Returns plain text suitable for the analyser.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from typing import Optional


@dataclass
class LoadedDocument:
    text: str
    source_path: str
    format: str            # "markdown" | "pdf" | "text"
    page_count: Optional[int] = None
    extraction_note: Optional[str] = None


def load(path: str | pathlib.Path) -> LoadedDocument:
    """Load a contract or audit document and return its text content."""
    p = pathlib.Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Document not found: {p}")
    suffix = p.suffix.lower()
    if suffix in (".md", ".markdown"):
        return _load_markdown(p)
    if suffix == ".txt":
        return _load_text(p)
    if suffix == ".pdf":
        return _load_pdf(p)
    # Fallback: try as plain text
    return _load_text(p)


def _load_markdown(p: pathlib.Path) -> LoadedDocument:
    text = p.read_text(encoding="utf-8")
    return LoadedDocument(text=text, source_path=str(p), format="markdown")


def _load_text(p: pathlib.Path) -> LoadedDocument:
    text = p.read_text(encoding="utf-8", errors="replace")
    return LoadedDocument(text=text, source_path=str(p), format="text")


def _load_pdf(p: pathlib.Path) -> LoadedDocument:
    try:
        return _load_pdf_pypdf(p)
    except Exception as e:
        try:
            return _load_pdf_pdfminer(p)
        except Exception as e2:
            raise RuntimeError(
                f"Could not extract text from PDF {p}. "
                f"pypdf error: {e}; pdfminer error: {e2}. "
                "Ensure the PDF contains selectable text (not a scanned image)."
            ) from e2


def _load_pdf_pypdf(p: pathlib.Path) -> LoadedDocument:
    import pypdf
    reader = pypdf.PdfReader(str(p))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    text = "\n\n".join(pages)
    if not text.strip():
        raise ValueError("pypdf extracted empty text — PDF may be image-only")
    return LoadedDocument(
        text=text,
        source_path=str(p),
        format="pdf",
        page_count=len(reader.pages),
        extraction_note="Extracted via pypdf",
    )


def _load_pdf_pdfminer(p: pathlib.Path) -> LoadedDocument:
    from pdfminer.high_level import extract_text
    text = extract_text(str(p))
    if not text.strip():
        raise ValueError("pdfminer extracted empty text — PDF may be image-only")
    return LoadedDocument(
        text=text,
        source_path=str(p),
        format="pdf",
        extraction_note="Extracted via pdfminer",
    )
