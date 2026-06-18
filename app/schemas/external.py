"""백엔드 연동 계약 v2.0 외부 스키마 (camelCase).

내부 ChatRequest/ChatResponse 와 분리해, 어댑터(app/services/contract.py)가 양쪽을 번역한다.
명세: 연동 명세서 v2.0 (POST /api/v1/chat, 동기 JSON).
"""
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ===== 요청 =====
class CompanyContext(BaseModel):
    corpCode: str
    corpName: str


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatV2Request(BaseModel):
    roomId: int
    userSeq: int
    companyContext: CompanyContext
    messages: list[ChatMessage] = Field(default_factory=list)


# ===== 응답 =====
class SourceItem(BaseModel):
    rceptNo: Optional[str] = None
    reportNm: Optional[str] = None
    rceptDt: Optional[str] = None
    sectionTitle: Optional[str] = None
    quote: str
    score: Optional[float] = None


class VerificationOut(BaseModel):
    verdict: str          # pass | partial | fail
    groundedScore: float  # 0~1


class ErrorOut(BaseModel):
    code: str
    message: str
    retriable: bool


class ChatV2Response(BaseModel):
    roomId: int
    intent: Optional[str] = None
    answerText: Optional[str] = None
    sourceContent: Optional[str] = None
    macroSnapshot: Optional[str] = None
    sources: Optional[list[SourceItem]] = None
    outOfScope: bool = False
    detectedCompany: Optional[str] = None
    needsClarification: bool = False
    verification: Optional[VerificationOut] = None
    error: Optional[ErrorOut] = None
