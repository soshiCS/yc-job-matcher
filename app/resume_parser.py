from __future__ import annotations

from io import BytesIO
from pathlib import Path
from typing import Iterable

from docx import Document
from pypdf import PdfReader


SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


class ResumeParsingError(Exception):
    pass


def extract_resume_text(filename: str, content: bytes) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ResumeParsingError(
            f"Unsupported resume type: {suffix}. Use PDF, DOCX, TXT, or MD."
        )

    if suffix == ".pdf":
        return _extract_pdf_text(content)
    if suffix == ".docx":
        return _extract_docx_text(content)
    return content.decode("utf-8", errors="ignore").strip()


def _extract_pdf_text(content: bytes) -> str:
    reader = PdfReader(BytesIO(content))
    pages = [page.extract_text() or "" for page in reader.pages]
    text = "\n".join(pages).strip()
    if not text:
        raise ResumeParsingError("Could not extract text from the PDF resume.")
    return text


def _extract_docx_text(content: bytes) -> str:
    doc = Document(BytesIO(content))
    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]
    text = "\n".join(paragraphs).strip()
    if not text:
        raise ResumeParsingError("Could not extract text from the DOCX resume.")
    return text


COMMON_SKILLS = {
    "python",
    "java",
    "javascript",
    "typescript",
    "react",
    "node",
    "fastapi",
    "postgres",
    "sql",
    "aws",
    "docker",
    "kubernetes",
    "c++",
    "go",
    "rust",
    "scala",
    "jvm",
    "compiler",
    "compilers",
    "bytecode",
    "observability",
    "distributed systems",
    "backend",
    "frontend",
    "machine learning",
    "ai",
    "llm",
    "prompting",
    "api",
    "apis",
    "startup",
    "infrastructure",
    "infra",
}


def extract_skill_keywords(text: str) -> list[str]:
    lowered = text.lower()
    found = []
    for skill in COMMON_SKILLS:
        if skill in lowered:
            found.append(skill)
    return sorted(set(found))
