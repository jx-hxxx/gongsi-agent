"""FastAPI 라우트.

- POST /analyze        : 공시 분석 (text / dart)
- POST /analyze/pdf    : 공시 분석 (PDF 업로드)
- GET  /analyses       : 분석 목록
- GET  /analyses/{id}  : 분석 상세
"""
from __future__ import annotations

import json

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Response, UploadFile

from app.schemas.external import ChatV2Request, ChatV2Response
from app.services import contract
from app.schemas.disclosure import (
    AnalysisListResponse,
    AnalysisResult,
    AnalyzeRequest,
    AskRequest,
    AskResponse,
    ChatRequest,
    ChatResponse,
    ChatTitleRequest,
    ChatTitleResponse,
    DisclosureSource,
)
from app.agents import crew
from app.services import chat, pipeline
from app.storage import db

router = APIRouter()


@router.post("/analyze", response_model=AnalysisResult)
def analyze(req: AnalyzeRequest) -> AnalysisResult:
    """공시 분석 (source=text 또는 dart)."""
    if req.source == DisclosureSource.PDF:
        raise HTTPException(400, "PDF 는 /analyze/pdf 엔드포인트를 사용하세요.")
    return pipeline.analyze(req)


@router.post("/analyze/pdf", response_model=AnalysisResult)
async def analyze_pdf(
    file: UploadFile = File(..., description="공시 PDF"),
    company_name: str | None = Form(None),
    title: str | None = Form(None),
    questions: str | None = Form(None, description="질문 JSON 배열 문자열"),
) -> AnalysisResult:
    """공시 PDF 업로드 분석."""
    try:
        q_list = json.loads(questions) if questions else []
        if not isinstance(q_list, list):
            raise ValueError
    except ValueError:
        raise HTTPException(400, "questions 는 JSON 배열 문자열이어야 합니다.")

    data = await file.read()
    req = AnalyzeRequest(
        source=DisclosureSource.PDF,
        company_name=company_name,
        title=title or file.filename,
        questions=q_list,
    )
    return pipeline.analyze(req, pdf_bytes=data)


@router.post("/v1/chat", response_model=ChatV2Response)  # 라우터 prefix "/api" + "/v1/chat" = /api/v1/chat
def chat_v2(
    req: ChatV2Request,
    response: Response,
    x_trace_id: str | None = Header(default=None),
) -> ChatV2Response:
    """백엔드 연동 계약 v2.0 — 동기 JSON. (Spring Boot → FastAPI)

    v2.0 요청을 내부 ChatRequest 로 번역 → handle_turn → v2.0 응답으로 변환.
    추론 실패도 200 + error 필드. X-Trace-Id 는 그대로 echo.
    """
    if x_trace_id:
        response.headers["X-Trace-Id"] = x_trace_id
    if not req.messages or req.messages[-1].role != "user":
        raise HTTPException(400, "messages 의 마지막 항목은 role=user 여야 합니다.")
    internal = contract.to_internal(req)
    result = chat.handle_turn(internal)
    return contract.to_v2(req.roomId, req.companyContext.corpName, result)


@router.post("/chat", response_model=ChatResponse)
def chat_turn(req: ChatRequest) -> ChatResponse:
    """핑퐁 채팅 한 턴 (기업 단위 방).

    백엔드가 (방 회사 corp_code/회사명 + 질문 + 단기 메모리 history)를 넘기면
    라우터 분류 → 스코프 가드 → 코퍼스 검색·근거 답변 → 검증을 거쳐 응답한다.
    세션/타이머/대화 저장은 백엔드 소관 (AI 는 stateless).
    """
    return chat.handle_turn(req)


@router.post("/chat/title", response_model=ChatTitleResponse)
def chat_title(req: ChatTitleRequest) -> ChatTitleResponse:
    """첫 질문을 짧은 대화 제목으로 요약 (마이페이지 목록 제목). 백엔드가 첫 턴에 1회 호출."""
    return ChatTitleResponse(title=crew.run_title(req.question))


@router.get("/analyses", response_model=AnalysisListResponse)
def list_analyses(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    company: str | None = Query(None, description="기업명 필터 (마이페이지 탭)"),
) -> AnalysisListResponse:
    """마이페이지 목록 (장기 메모리 조회). company 로 기업별 필터."""
    items, total = db.list_analyses(limit=limit, offset=offset, company=company)
    return AnalysisListResponse(items=items, total=total)


@router.get("/companies", response_model=list[str])
def list_companies() -> list[str]:
    """마이페이지 탭 구성용 — 분석된 기업 목록."""
    return db.list_companies()


@router.get("/analyses/{analysis_id}", response_model=AnalysisResult)
def get_analysis(analysis_id: str) -> AnalysisResult:
    result = db.get_analysis(analysis_id)
    if not result:
        raise HTTPException(404, "분석을 찾을 수 없습니다.")
    return result


@router.post("/analyses/{analysis_id}/ask", response_model=AskResponse)
def ask(analysis_id: str, req: AskRequest) -> AskResponse:
    """분석된 공시에 대한 후속 질문 (대화형, 단기 메모리).

    req.history 를 주면 그 맥락을 쓰고, 비어 있으면 서버 저장 대화 내역을 사용한다.
    """
    try:
        return pipeline.ask(analysis_id, req.question, history=req.history)
    except ValueError as e:
        raise HTTPException(404, str(e))
