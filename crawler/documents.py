"""
Document text extraction — PDFs, HTML and plain text from filings/IR pages.

Hard limits keep the run bounded:
  * max_pages: only the first N pages of a PDF are read (outcomes, minutes and
    agreement summaries put the substance up front).
  * a per-document time budget; extraction stops when it is spent.
Scanned (image-only) PDFs return empty text — we do not OCR, and we never
invent content for them; they are recorded with text_status="no_text_layer".
"""
from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass

from bs4 import BeautifulSoup

MAX_PDF_PAGES = 25
MAX_TEXT_CHARS = 200_000
PDF_TIME_BUDGET = 25  # seconds


@dataclass
class ExtractedDoc:
    text: str
    status: str          # ok | truncated | no_text_layer | unsupported | error | empty
    pages_read: int = 0
    kind: str = ""       # pdf | html | text | xml


def _clean(text: str) -> str:
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_pdf(data: bytes, max_pages: int = MAX_PDF_PAGES) -> ExtractedDoc:
    try:
        import pdfplumber
    except ImportError:
        return ExtractedDoc("", "unsupported", 0, "pdf")
    started = time.monotonic()
    parts: list[str] = []
    pages_read = 0
    status = "ok"
    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            total = len(pdf.pages)
            for page in pdf.pages[:max_pages]:
                if time.monotonic() - started > PDF_TIME_BUDGET:
                    status = "truncated"
                    break
                parts.append(page.extract_text() or "")
                pages_read += 1
            if total > max_pages and status == "ok":
                status = "truncated"
    except Exception as e:
        return ExtractedDoc("", f"error:{type(e).__name__}", pages_read, "pdf")
    text = _clean("\n".join(parts))
    if not text:
        status = "no_text_layer" if pages_read else "empty"
    return ExtractedDoc(text[:MAX_TEXT_CHARS], status, pages_read, "pdf")


def extract_html(html: str) -> ExtractedDoc:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav", "svg"]):
        tag.decompose()
    text = _clean(soup.get_text("\n"))
    return ExtractedDoc(text[:MAX_TEXT_CHARS], "ok" if text else "empty", 0, "html")


def extract_any(content: bytes, content_type: str = "", url: str = "") -> ExtractedDoc:
    ct = (content_type or "").lower()
    u = url.lower().split("?")[0]
    if not content:
        return ExtractedDoc("", "empty")
    if "pdf" in ct or u.endswith(".pdf") or content[:5] == b"%PDF-":
        return extract_pdf(content)
    if "html" in ct or u.endswith((".htm", ".html")) or b"<html" in content[:2000].lower():
        return extract_html(content.decode("utf-8", errors="replace"))
    if "xml" in ct or u.endswith(".xml"):
        return ExtractedDoc(content.decode("utf-8", errors="replace")[:MAX_TEXT_CHARS], "ok", 0, "xml")
    if ct.startswith("text/") or u.endswith(".txt"):
        return ExtractedDoc(_clean(content.decode("utf-8", errors="replace"))[:MAX_TEXT_CHARS], "ok", 0, "text")
    return ExtractedDoc("", "unsupported")
