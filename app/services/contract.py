"""v2.0 계약 ↔ 내부 스키마 변환 어댑터.

요청: v2.0(roomId/companyContext/messages) → 내부 ChatRequest
응답: 내부 ChatResponse → v2.0(camelCase, sourceContent/sources/error)
"""
from __future__ import annotations

from app.schemas.disclosure import ChatRequest, ChatResponse, ChatTurn, Citation
from app.schemas.external import (
    ChatV2Request,
    ChatV2Response,
    ErrorOut,
    SourceItem,
    VerificationOut,
)
from app.services import macro

_MAX_HISTORY = 10  # 단기 메모리 안전 상한 (최근 메시지 N개만 사용)
_DART_VIEWER = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo="  # 원문 뷰어


def _dart_url(rcept_no: str | None) -> str | None:
    return f"{_DART_VIEWER}{rcept_no}" if rcept_no else None


def to_internal(req: ChatV2Request) -> ChatRequest:
    """v2.0 요청 → 내부 ChatRequest. 마지막 user 메시지=질문, 나머지=history."""
    msgs = req.messages
    question = msgs[-1].content if msgs else ""
    history = [ChatTurn(role=m.role, content=m.content) for m in msgs[:-1]]
    history = history[-_MAX_HISTORY:]  # 최근 N개만
    return ChatRequest(
        corp_code=req.companyContext.corpCode,
        company_name=req.companyContext.corpName,
        question=question,
        history=history,
        session_id=str(req.roomId),
    )


def _format_source_content(corp_name: str, cits: list[Citation]) -> str:
    """sources → §4.5 포맷 텍스트. '[접수번호 / 기업명 공시명]\\n인용' 을 빈 줄로 구분."""
    blocks = []
    for c in cits:
        head = f"[{c.rcept_no or '-'} / {corp_name} {c.report_nm or '-'}]"
        blocks.append(f"{head}\n{c.quote}")
    return "\n\n".join(blocks)


def to_v2(room_id: int, corp_name: str, r: ChatResponse) -> ChatV2Response:
    """내부 ChatResponse → v2.0 응답."""
    # 완전 실패(답 자체를 못 만든 경우) → error 채우고 answerText=null.
    # 부분 실패(검증/거시/재무 sub-step)는 error 로 올리지 않음(로그만) — §2-1.
    if r.error and "qa_failed" in r.error:
        return ChatV2Response(
            roomId=room_id,
            intent=(r.intent.value if r.intent else None),
            answerText=None,
            error=ErrorOut(code="INTERNAL_ERROR", message=r.error, retriable=True),
        )

    # 거시 합성 출처(macro-ecos)는 sources 에서 제외 → macroSnapshot 으로만 노출(중복 방지)
    src_cits = [c for c in r.citations if c.chunk_id != "macro-ecos"]
    sources = [
        SourceItem(
            rceptNo=c.rcept_no, reportNm=c.report_nm, rceptDt=c.rcept_dt,
            sectionTitle=c.section_title, quote=c.quote, score=c.score,
            dartUrl=_dart_url(c.rcept_no),
        )
        for c in src_cits
    ] or None
    source_content = _format_source_content(corp_name, src_cits) or None
    macro_snapshot = macro.format_macro(r.macro) if (r.macro_used and r.macro) else None
    verification = (
        VerificationOut(
            verdict=r.verification.verdict.value,
            groundedScore=r.verification.grounded_score,
        )
        if r.verification else None
    )
    return ChatV2Response(
        roomId=room_id,
        intent=r.intent.value if r.intent else None,
        answerText=r.answer or None,
        sourceContent=source_content,
        macroSnapshot=macro_snapshot,
        sources=sources,
        outOfScope=r.out_of_scope,
        detectedCompany=r.detected_company,
        needsClarification=r.needs_clarification,
        verification=verification,
        error=None,
    )
