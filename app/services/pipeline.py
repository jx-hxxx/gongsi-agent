"""분석 파이프라인 오케스트레이션.

공시 입력 → 원문 적재 → 청킹 → 임베딩/저장 → Summary → QA → Verification → 결과 저장
"""
from __future__ import annotations

import uuid

from app.agents import crew
from app.config import get_settings
from app.data import dart, loaders
from app.rag.chunker import split_into_chunks
from app.rag.vectorstore import get_vector_store
from app.schemas.disclosure import (
    AnalysisResult,
    AnalysisStatus,
    AnalyzeRequest,
    AskResponse,
    ChatTurn,
    DisclosureSource,
    QAResult,
    VerificationResult,
)
from app.storage import db


def _resolve_text(req: AnalyzeRequest, pdf_bytes: bytes | None) -> str:
    if req.source == DisclosureSource.DART:
        if not req.rcept_no:
            raise ValueError("source=dart 이면 rcept_no 가 필요합니다.")
        return dart.fetch_document_text(req.rcept_no)
    if req.source == DisclosureSource.PDF:
        if not pdf_bytes:
            raise ValueError("source=pdf 이면 PDF 파일이 필요합니다.")
        return loaders.load_pdf_text(pdf_bytes)
    # default: text
    text = loaders.load_text(req.raw_text or "")
    if not text:
        raise ValueError("raw_text 가 비어 있습니다.")
    return text


def analyze(
    req: AnalyzeRequest,
    *,
    pdf_bytes: bytes | None = None,
    golden_answers: dict[str, str] | None = None,
) -> AnalysisResult:
    """공시 1건을 분석하고 결과를 저장한 뒤 반환한다.

    golden_answers: {질문: 정답} 형태의 골든셋 (검증 단계에서 비교용, 선택)
    """
    settings = get_settings()
    analysis_id = uuid.uuid4().hex[:16]
    golden_answers = golden_answers or {}

    result = AnalysisResult(
        analysis_id=analysis_id,
        status=AnalysisStatus.PENDING,
        company_name=req.company_name,
        title=req.title,
        source=req.source,
        rcept_no=req.rcept_no,
    )

    try:
        # 1) 원문 적재
        text = _resolve_text(req, pdf_bytes)

        # 2) 청킹 + 임베딩 저장
        chunks = split_into_chunks(
            text, chunk_size=settings.chunk_size, overlap=settings.chunk_overlap
        )
        if not chunks:
            raise ValueError("청킹 결과가 비었습니다 (원문 확인 필요).")
        get_vector_store().index_chunks(analysis_id, chunks)

        # 3) Summary Agent
        summary = crew.run_summary(analysis_id, text)
        result.summary = summary

        # 3-1) 요약 검증
        result.verifications.append(
            crew.run_verification(
                target="summary",
                answer=summary.summary,
                citations=summary.citations,
            )
        )

        # 4) QA Agent (질문별)
        qa_results: list[QAResult] = []
        verifications: list[VerificationResult] = []
        for q in req.questions:
            qa = crew.run_qa(analysis_id, q)
            qa_results.append(qa)
            verifications.append(
                crew.run_verification(
                    target=q,
                    answer=qa.answer,
                    citations=qa.citations,
                    golden_answer=golden_answers.get(q),
                )
            )
        result.qa = qa_results
        result.verifications.extend(verifications)

        # 초기 질의응답도 대화 내역으로 저장 → 이후 후속 질문에서 맥락으로 사용
        turns: list[ChatTurn] = []
        for qa in qa_results:
            turns.append(ChatTurn(role="user", content=qa.question))
            turns.append(ChatTurn(role="assistant", content=qa.answer))
        if turns:
            db.add_chat_turns(analysis_id, turns)

        result.status = AnalysisStatus.DONE

    except Exception as e:  # 파이프라인 실패도 기록
        result.status = AnalysisStatus.FAILED
        result.error = f"{type(e).__name__}: {e}"

    db.save_analysis(result)
    return result


def ask(analysis_id: str, question: str, history: list[ChatTurn] | None = None) -> AskResponse:
    """이미 분석된 공시에 대한 후속 질문 (대화형, 단기 메모리 사용).

    history 가 비어 있으면 서버에 저장된 해당 공시의 대화 내역을 불러와 맥락으로 쓴다.
    """
    if db.get_analysis(analysis_id) is None:
        raise ValueError(f"분석을 찾을 수 없습니다: {analysis_id}")

    # 단기 메모리: 전달된 history 우선, 없으면 저장된 대화 내역 사용
    convo = history if history else db.get_chat_history(analysis_id)

    qa = crew.run_qa(analysis_id, question, history=convo)
    verification = crew.run_verification(
        target=question, answer=qa.answer, citations=qa.citations
    )

    # 이번 턴을 대화 내역에 추가
    db.add_chat_turns(
        analysis_id,
        [ChatTurn(role="user", content=question),
         ChatTurn(role="assistant", content=qa.answer)],
    )
    return AskResponse(analysis_id=analysis_id, qa=qa, verification=verification)
