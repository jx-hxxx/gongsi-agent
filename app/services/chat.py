"""핑퐁 채팅 턴 핸들러.

기업 단위 방에서 한 턴을 처리한다:
  라우터 분류 → (스코프/스몰토크 분기) → 코퍼스 검색 + QA → 검증 → 응답

설계 문서: 노션 "채팅 아키텍처 (확정)" §4(스코프 가드), §5-1(라우터), §6(에이전트).
AI 는 stateless — 세션/타이머/대화방은 백엔드가 소유하고, 여기서는 (질문 + history +
회사)만 받아 답을 만든다.
"""
from __future__ import annotations

import datetime

from app.agents import crew
from app.config import get_settings
from app.rag.vectorstore import get_vector_store
from app.services import financials, macro
from app.schemas.disclosure import (
    ChatIntent,
    ChatRequest,
    ChatResponse,
    ChatTurn,
    Citation,
)


def corpus_collection(corp_code: str) -> str:
    # ingest_corpus.corpus_collection 과 동일한 규칙 (corpus_<corp_code>)
    return f"corpus_{corp_code}"




def handle_turn(req: ChatRequest) -> ChatResponse:
    """채팅 한 턴을 처리해 응답을 반환한다."""
    settings = get_settings()
    history: list[ChatTurn] = req.history or []

    resp = ChatResponse(
        corp_code=req.corp_code,
        session_id=req.session_id,  # 받은 값 그대로 echo (모든 반환 경로 공통)
        intent=ChatIntent.QA,  # 임시값, 라우터 결과로 덮어씀
        answer="",
    )

    today = datetime.date.today().strftime("%Y%m%d")

    # 1) 라우터 — 의도/스코프/거시 관련성/기간 분류
    try:
        route = crew.run_router(
            req.question, req.company_name, history=history, today=today
        )
    except Exception as e:
        # 라우터 실패 시 보수적으로 QA 로 진행 (전체 중단 방지)
        resp.error = f"router_failed: {type(e).__name__}: {e}"
        return _run_qa_turn(
            req, history, macro_relevant=False, financial_relevant=False,
            search_query=None, date_from=None, date_to=None, prefer_recent=False,
            resp=resp,
        )

    resp.intent = route.intent

    # 2) 스코프 가드 — 다른 회사/비교 질문이면 답하지 않고 팝업 신호
    if route.out_of_scope or route.intent == ChatIntent.OUT_OF_SCOPE:
        resp.out_of_scope = True
        resp.detected_company = route.detected_company
        other = route.detected_company or "다른 회사"
        resp.answer = (
            f"이 방은 '{req.company_name}' 전용입니다. "
            f"'{other}' 관련 질문은 해당 회사 상세페이지에서 해주세요."
        )
        return resp

    # 3) 스몰토크 — 근거/검증 없이 짧게 답
    if route.intent == ChatIntent.SMALLTALK:
        resp.answer = route.reply or "안녕하세요. 공시에 대해 궁금한 점을 물어봐 주세요."
        return resp

    # 4) qa / summary / macro — 코퍼스 검색 + 근거 기반 답변 + 검증
    return _run_qa_turn(
        req, history,
        macro_relevant=route.macro_relevant,
        financial_relevant=route.financial_relevant,
        search_query=route.search_query,
        date_from=route.date_from,
        date_to=route.date_to,
        prefer_recent=route.prefer_recent,
        resp=resp,
    )


def _add_err(resp: ChatResponse, msg: str) -> None:
    resp.error = f"{resp.error} | {msg}" if resp.error else msg


def _gather(req, history, *, query, top_k, date_from, date_to, prefer_recent,
            financial_relevant, macro_relevant, resp):
    """근거 수집(코퍼스 + 재무 + 거시) → QA 통합 답변. (qa, macro_snapshot) 반환.

    retrieve-then-read: 검색은 코드가 결정적으로 끝내고, 그 근거 안에서만 답하게 한다.
    """
    store = get_vector_store()
    retrieved = store.search_corpus(
        corpus_collection(req.corp_code), query, top_k=top_k,
        date_from=date_from, date_to=date_to, prefer_recent=prefer_recent,
    )
    # 재무결합 (정형 재무 수치 → 근거, 앞쪽 우선)
    if financial_relevant:
        try:
            fin = financials.get_financial_citations(req.corp_code)
            if fin:
                retrieved = fin + retrieved
        except Exception as e:
            _add_err(resp, f"financials_failed: {type(e).__name__}: {e}")
    # 거시결합 (공시일 기준 ECOS → 근거, 통합 답변용)
    snapshot = None
    if macro_relevant:
        as_of = max((c.rcept_dt for c in retrieved if c.rcept_dt), default=None) \
            or datetime.date.today().strftime("%Y%m%d")
        try:
            s = macro.get_macro(as_of)
            if macro.has_value(s):
                retrieved = retrieved + [
                    Citation(
                        chunk_id="macro-ecos",
                        section_title="거시지표(ECOS, 같은 시점 사실)",
                        quote=macro.format_macro(s),
                        rcept_dt=as_of,
                        report_nm="ECOS 거시지표",
                    )
                ]
                snapshot = s
        except Exception as e:
            _add_err(resp, f"macro_failed: {type(e).__name__}: {e}")
    qa = crew.run_chat_qa(req.question, retrieved, history=history)
    return qa, snapshot


def _finalize(req, resp, qa, macro_snapshot) -> str:
    """qa 결과를 resp 에 반영하고 검증 실행. verdict 문자열 반환(pass/partial/fail/error)."""
    resp.answer = qa.answer
    resp.citations = qa.citations if qa.answerable else []
    if macro_snapshot is not None:
        resp.macro = macro_snapshot
        resp.macro_used = True
    try:
        resp.verification = crew.run_verification(
            target=req.question, answer=resp.answer, citations=resp.citations,
        )
        return resp.verification.verdict.value
    except Exception as e:
        _add_err(resp, f"verification_failed: {type(e).__name__}: {e}")
        return "error"


def _run_qa_turn(
    req: ChatRequest,
    history: list[ChatTurn],
    *,
    macro_relevant: bool,
    financial_relevant: bool,
    search_query: str | None,
    date_from: str | None,
    date_to: str | None,
    prefer_recent: bool,
    resp: ChatResponse,
) -> ChatResponse:
    """근거 기반 답변 턴. retrieve-then-read + Corrective RAG(fail 시 넓혀서 1회 재검색).

    부분 실패 격리: 재무/거시/검증 각각 try/except → 한 곳이 터져도 답변은 살린다.
    """
    settings = get_settings()

    # 1차 시도 — 라우터가 정제한 검색어 + 날짜필터 + 최신우선
    query = (search_query or "").strip() or req.question
    try:
        qa, snap = _gather(
            req, history, query=query, top_k=settings.top_k,
            date_from=date_from, date_to=date_to, prefer_recent=prefer_recent,
            financial_relevant=financial_relevant, macro_relevant=macro_relevant, resp=resp,
        )
    except Exception as e:
        resp.answer = "일시적인 오류로 답변을 생성하지 못했습니다. 다시 시도해 주세요."
        _add_err(resp, f"qa_failed: {type(e).__name__}: {e}")
        return resp

    # 되묻기 → 검증·재검색 생략하고 바로 반환
    if qa.needs_clarification:
        resp.needs_clarification = True
        resp.answer = qa.answer
        resp.citations = qa.citations
        return resp

    verdict = _finalize(req, resp, qa, snap)

    # 2차 (Corrective RAG) — fail 이면 '더 넓게' 재검색 후 재답변·재검증.
    #   같은 쿼리면 결과가 같으므로, 원본 질문 + top_k 확대 + 날짜필터 해제로 그물을 넓힌다.
    if verdict == "fail":
        try:
            qa2, snap2 = _gather(
                req, history, query=req.question, top_k=max(settings.top_k * 3, 15),
                date_from=None, date_to=None, prefer_recent=False,
                financial_relevant=financial_relevant, macro_relevant=macro_relevant, resp=resp,
            )
            if not qa2.needs_clarification:
                verdict = _finalize(req, resp, qa2, snap2)  # resp 를 2차 결과로 갱신
        except Exception as e:
            _add_err(resp, f"retry_failed: {type(e).__name__}: {e}")
        # 재검색 후에도 fail 이면 사용자에게 신뢰도 경고를 붙인다
        if verdict == "fail":
            resp.answer = "⚠️ 근거 충실도가 낮은 답변입니다(확인 필요).\n\n" + resp.answer

    return resp
