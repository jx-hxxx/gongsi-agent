"""공시 원문을 검색 가능한 청크로 분할한다.

공시는 섹션 제목(예: "1. 계약 내용", "II. 주요 사항")이 있는 경우가 많아
섹션을 인식해 청크 메타데이터에 section_title 을 남긴다. 이는 출처 표시에 쓰인다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class Chunk:
    chunk_id: str
    text: str
    section_title: str | None = None
    order: int = 0
    meta: dict = field(default_factory=dict)


# "1.", "1)", "가.", "Ⅰ.", "II.", "제1조" 등 흔한 섹션 머리표
_SECTION_RE = re.compile(
    r"^\s*("
    r"제?\s*\d+\s*[조항호]"               # 제1조, 1항
    r"|[0-9]+[\.\)]"                       # 1. 1)
    r"|[ⅠⅡⅢⅣⅤ]+[\.\)]"                  # 로마자
    r"|[IVX]+[\.\)]"                       # 영문 로마자
    r"|[가-힣][\.\)]"                      # 가. 나.
    r")\s*\S+",
)


def _looks_like_section(line: str) -> bool:
    line = line.strip()
    if not line or len(line) > 60:
        return False
    return bool(_SECTION_RE.match(line))


def split_into_chunks(
    text: str,
    *,
    chunk_size: int = 800,
    overlap: int = 120,
    id_prefix: str = "c",
) -> list[Chunk]:
    """텍스트를 섹션 인식 + 슬라이딩 윈도우로 청킹한다."""
    text = _normalize(text)
    if not text:
        return []

    # 1) 섹션 경계로 1차 분할
    blocks: list[tuple[str | None, str]] = []  # (section_title, body)
    current_title: str | None = None
    buffer: list[str] = []

    for line in text.split("\n"):
        if _looks_like_section(line):
            if buffer:
                blocks.append((current_title, "\n".join(buffer).strip()))
                buffer = []
            current_title = line.strip()
        else:
            buffer.append(line)
    if buffer:
        blocks.append((current_title, "\n".join(buffer).strip()))

    # 2) 각 블록을 chunk_size 기준으로 2차 분할 (긴 섹션 대응)
    chunks: list[Chunk] = []
    idx = 0
    for section_title, body in blocks:
        if not body:
            continue
        for piece in _window(body, chunk_size, overlap):
            chunks.append(
                Chunk(
                    chunk_id=f"{id_prefix}-{idx:04d}",
                    text=piece,
                    section_title=section_title,
                    order=idx,
                )
            )
            idx += 1
    return chunks


def _window(text: str, size: int, overlap: int) -> list[str]:
    if len(text) <= size:
        return [text]
    step = max(size - overlap, 1)
    out: list[str] = []
    for start in range(0, len(text), step):
        piece = text[start : start + size].strip()
        if piece:
            out.append(piece)
        if start + size >= len(text):
            break
    return out


def _normalize(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()
