"""공시 원문 적재 (대체 데이터 경로).

- PDF 업로드: DART 웹에서 직접 받은 PDF 를 텍스트로 변환
- 텍스트 직접 입력: 그대로 통과
"""
from __future__ import annotations

import io


def load_pdf_text(data: bytes) -> str:
    """PDF 바이트에서 텍스트 추출."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n\n".join(pages).strip()


def load_text(raw: str) -> str:
    return (raw or "").strip()
