"""백엔드 연동용 JSON 응답 스키마.

이 스키마가 AI 파트와 백엔드 사이의 계약(contract)이다.
모든 답변에는 근거 문단(Citation)이 따라붙어 출처를 추적할 수 있다.

주의: CrewAI(output_pydantic)가 model.__annotations__ 를 날것으로 읽으므로
`from __future__ import annotations` 를 쓰면 안 된다(어노테이션이 문자열이 되어 깨짐).
"""
from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field


# ============================================================
# 입력
# ============================================================
class DisclosureSource(str, Enum):
    """공시 원문을 어디서 받았는지."""
    TEXT = "text"          # 텍스트 직접 입력
    PDF = "pdf"            # PDF 업로드 (대체 데이터)
    DART = "dart"          # OpenDART API 조회


class AnalyzeRequest(BaseModel):
    """공시 1건 분석 요청."""
    source: DisclosureSource = DisclosureSource.TEXT
    # source=text 일 때
    raw_text: Optional[str] = Field(None, description="공시 원문 텍스트")
    # source=dart 일 때
    rcept_no: Optional[str] = Field(None, description="DART 접수번호(14자리)")
    # 공통 메타
    company_name: Optional[str] = None
    title: Optional[str] = Field(None, description="공시 제목")
    # 질문 목록 (없으면 요약만 수행)
    questions: list[str] = Field(default_factory=list)


# ============================================================
# 근거 / 출처
# ============================================================
class Citation(BaseModel):
    """답변·요약의 근거가 된 공시 문단 출처."""
    chunk_id: str = Field(..., description="청크 식별자")
    section_title: Optional[str] = Field(None, description="해당 문단이 속한 섹션 제목")
    quote: str = Field(..., description="근거가 된 원문 인용")
    score: Optional[float] = Field(None, description="검색 유사도 점수")
    # 어느 공시에서 나온 근거인지 (코퍼스 검색 시 채워짐) — RFP 2.1 출처 추적
    rcept_no: Optional[str] = Field(None, description="출처 공시 접수번호")
    report_nm: Optional[str] = Field(None, description="출처 공시명")
    rcept_dt: Optional[str] = Field(None, description="출처 공시 접수일(YYYYMMDD)")


# ============================================================
# Agent 별 결과
# ============================================================
class SummaryResult(BaseModel):
    """Summary Agent 결과."""
    headline: str = Field(..., description="한 줄 핵심")
    key_points: list[str] = Field(default_factory=list, description="핵심 bullet")
    summary: str = Field(..., description="본문 요약")
    citations: list[Citation] = Field(default_factory=list)


class QAResult(BaseModel):
    """QA Agent 결과 (질문 1건)."""
    question: str
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    answerable: bool = Field(True, description="공시 내 근거로 답변 가능했는지")
    needs_clarification: bool = Field(
        False,
        description="근거 후보가 여러 개고 기준(당분기/누적·연결/별도 등)이 모호해 되물어야 하는지",
    )


class VerificationVerdict(str, Enum):
    PASS = "pass"            # 근거에 부합
    PARTIAL = "partial"      # 일부만 근거 있음
    FAIL = "fail"            # 근거 없음 / 환각 의심


class VerificationResult(BaseModel):
    """Verification Agent 결과 (답변 1건에 대한 검증)."""
    target: str = Field(..., description="검증 대상 (summary | 질문 텍스트)")
    verdict: VerificationVerdict
    grounded_score: float = Field(..., ge=0, le=1, description="근거 충실도 0~1")
    reason: str = Field(..., description="판정 근거")
    issues: list[str] = Field(default_factory=list, description="발견된 문제점")


# ============================================================
# 최종 응답
# ============================================================
class AnalysisStatus(str, Enum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"


class AnalysisResult(BaseModel):
    """분석 1건 전체 결과 (저장 / 상세 조회 단위)."""
    analysis_id: str
    status: AnalysisStatus = AnalysisStatus.DONE
    company_name: Optional[str] = None
    title: Optional[str] = None
    source: DisclosureSource = DisclosureSource.TEXT
    rcept_no: Optional[str] = None

    summary: Optional[SummaryResult] = None
    qa: list[QAResult] = Field(default_factory=list)
    verifications: list[VerificationResult] = Field(default_factory=list)

    error: Optional[str] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class AnalysisListItem(BaseModel):
    """목록 조회용 요약 항목 (마이페이지 카드)."""
    analysis_id: str
    company_name: Optional[str] = None
    title: Optional[str] = None
    headline: Optional[str] = Field(None, description="요약 한 줄 (마이페이지 SUMMARY 칸)")
    status: AnalysisStatus
    created_at: datetime


class AnalysisListResponse(BaseModel):
    items: list[AnalysisListItem]
    total: int


# ============================================================
# 대화형 QA (단기 메모리)
# ============================================================
# 단기 메모리 = "한 공시(세션) 안에서의 대화 맥락 유지".
# 사용자가 '그거/방금 그것' 같이 가리키면 직전 대화를 참고해 해석한다.
# (장기 메모리=사용자 취향 기억은 객관성 유지를 위해 의도적으로 도입하지 않는다.)
class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class AskRequest(BaseModel):
    """이미 분석된 공시에 대한 후속 질문 (대화형)."""
    question: str
    # 백엔드가 이전 대화를 넘겨주면 그대로 사용한다.
    # 비어 있으면 서버에 저장된 해당 공시의 대화 내역을 불러와 맥락으로 쓴다.
    history: list[ChatTurn] = Field(default_factory=list)


class AskResponse(BaseModel):
    analysis_id: str
    qa: QAResult
    verification: VerificationResult


# ============================================================
# 핑퐁 채팅 (기업 단위 방 + 라우터)
# ============================================================
class ChatIntent(str, Enum):
    """라우터가 분류하는 질문 의도."""
    SMALLTALK = "smalltalk"        # 인사·잡담·감사 → 근거 불필요
    QA = "qa"                      # 특정 사실/근거 질문
    SUMMARY = "summary"            # 요약·최근 동향
    MACRO = "macro"                # 환율·금리·시장 등 거시 질문
    OUT_OF_SCOPE = "out_of_scope"  # 방 회사가 아닌 다른 회사/비교 → 팝업


class RouterResult(BaseModel):
    """라우터 에이전트 결과 (매 턴 맨 앞에서 분류)."""
    intent: ChatIntent
    needs_evidence: bool = Field(True, description="공시 근거(출처)가 필요한 질문인지")
    macro_relevant: bool = Field(False, description="거시지표 결합이 도움되는 질문인지")
    financial_relevant: bool = Field(
        False, description="매출·영업이익·순이익 등 정형 재무 수치/실적 비교 질문인지"
    )
    out_of_scope: bool = Field(False, description="방 회사가 아닌 다른 회사가 주제인지")
    detected_company: Optional[str] = Field(None, description="out_of_scope일 때 감지된 회사명")
    reply: Optional[str] = Field(None, description="smalltalk일 때 짧은 답변 (그 외 비움)")
    search_query: Optional[str] = Field(
        None,
        description="검색에 쓸 정제된 키워드 (회사명·말투·시간표현 제거). qa/summary/macro일 때만 채움",
    )
    # 시간 표현 → 날짜 메타필터 (rcept_dt 기준). 임베딩 대신 날짜로 정확히 거른다.
    date_from: Optional[str] = Field(None, description="기간 시작 YYYYMMDD (없으면 null)")
    date_to: Optional[str] = Field(None, description="기간 끝 YYYYMMDD (없으면 null)")
    prefer_recent: bool = Field(False, description="'최근/가장 최근/요즘' 등 최신 우선 여부")


class ChatRequest(BaseModel):
    """핑퐁 채팅 한 턴 요청 (백엔드 → AI)."""
    corp_code: str = Field(..., description="방의 회사 DART corp_code")
    company_name: str = Field(..., description="방의 회사명 (스코프 판단/안내문구용)")
    question: str
    history: list[ChatTurn] = Field(default_factory=list, description="단기 메모리 (세션 내 직전 대화)")
    session_id: Optional[str] = Field(
        None,
        description="세션 식별자(추적·로그용). AI는 상태 저장 없이 응답에 그대로 echo한다.",
    )


class ChatTitleRequest(BaseModel):
    """대화 제목 생성 요청 (백엔드가 첫 질문으로 1회 호출)."""
    question: str


class ChatTitleResponse(BaseModel):
    title: str


class ChatResponse(BaseModel):
    """핑퐁 채팅 한 턴 응답 (AI → 백엔드)."""
    corp_code: str
    session_id: Optional[str] = Field(None, description="요청의 session_id 를 그대로 echo (요청-응답 매칭·로그용)")
    intent: ChatIntent
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    verification: Optional[VerificationResult] = None
    out_of_scope: bool = False
    detected_company: Optional[str] = None
    macro_used: bool = False
    macro: Optional[dict] = Field(
        None, description="결합에 쓴 거시 스냅샷(환율·금리·KOSPI, 공시일 기준). 프론트 표시용"
    )
    needs_clarification: bool = Field(
        False, description="되묻는 중인지 (answer 가 사용자에게 던지는 확인 질문)"
    )
    error: Optional[str] = Field(None, description="부분 실패 시 사유 (전체 중단은 아님)")
