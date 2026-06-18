"""CrewAI 기반 3-Agent 구성.

- Summary Agent      (Sonnet)  : 공시 핵심 요약
- QA Agent           (Sonnet)  : 근거(RAG) 기반 질의응답
- Verification Agent (Opus)    : 답변의 근거 충실도 검증 (Double LLM Orchestration)

각 Agent 는 단일-에이전트 Crew 로 실행하고 결과를 Pydantic 스키마로 받는다.
"""
from __future__ import annotations

import os

import litellm
from crewai import Agent, Crew, LLM, Process, Task

# o4-mini 같은 추론 모델은 'stop'/'temperature' 등 일부 파라미터를 거부한다.
# litellm 이 지원하지 않는 파라미터를 자동으로 제거하도록 설정.
litellm.drop_params = True

from app.config import get_settings
from app.rag.vectorstore import get_vector_store
from app.schemas.disclosure import (
    ChatTurn,
    Citation,
    QAResult,
    RouterResult,
    SummaryResult,
    VerificationResult,
    VerificationVerdict,
)

# 요약 시 에이전트에 직접 넣어줄 본문 최대 길이 (전체 조망용)
_MAX_OVERVIEW_CHARS = 12000


def _llm(model: str) -> LLM:
    settings = get_settings()
    # litellm 일부 경로는 환경변수를 직접 읽으므로 함께 세팅
    os.environ["OPENAI_API_KEY"] = settings.openai_api_key
    # o4-mini 등 추론 모델은 'stop' 미지원 → litellm 이 해당 파라미터를 빼도록 지정
    return LLM(
        model=model,
        api_key=settings.openai_api_key,
        additional_drop_params=["stop"],
    )


# ============================================================
# Router Agent — 매 턴 맨 앞 관문 (의도 + 스코프 + 거시 관련성)
# ============================================================
def run_router(
    question: str,
    company_name: str,
    history: list[ChatTurn] | None = None,
    today: str | None = None,
) -> RouterResult:
    """질문을 분류한다. 근거가 필요한 질문인지, 다른 회사 질문인지 등을 판단.

    검색·답변을 하기 전에 호출해 분기 신호를 얻는다 (LLM 분류만, 툴 없음).
    today(YYYYMMDD)를 받으면 '작년/최근 3개월' 같은 표현을 실제 날짜로 환산한다.
    """
    import datetime

    settings = get_settings()
    history_block = _format_history(history)
    today = today or datetime.date.today().strftime("%Y%m%d")

    agent = Agent(
        role="질문 분류 라우터",
        goal=(
            f"'{company_name}' 전용 채팅방에서 사용자 질문을 분류한다. "
            "근거(출처)가 필요한 질문인지, 이 방 회사가 아닌 다른 회사를 묻는지 정확히 판단한다."
        ),
        backstory="너는 질문의 의도만 빠르게 가려내는 분류기다. 답변 본문은 만들지 않는다.",
        llm=_llm(settings.litellm_router_model),
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=(
            f"이 방은 '{company_name}' 전용이다.\n"
            "{history_block}"
            "사용자 질문: {question}\n\n"
            "다음 기준으로 분류하라.\n"
            "- intent:\n"
            "    smalltalk = 인사·감사·잡담 등 공시와 무관한 말\n"
            "    qa = 특정 사실·수치·사건에 대한 질문\n"
            "    summary = 요약/최근 동향/추세 요청\n"
            "    macro = 환율·금리·KOSPI 등 거시지표와 직접 관련된 질문\n"
            f"    out_of_scope = '{company_name}'가 아닌 다른 회사를 묻거나 두 회사 비교를 요청\n"
            "- needs_evidence: 공시 근거가 필요하면 true (qa/summary/macro=true, smalltalk/out_of_scope=false)\n"
            "- macro_relevant: 실적 해석·추세·전망이거나 거시지표 결합이 도움되면 true, "
            "단순 사실 조회면 false\n"
            f"- out_of_scope: 주제가 '{company_name}'가 아니면 true\n"
            "- detected_company: out_of_scope일 때 감지된 다른 회사명, 아니면 null\n"
            "- reply: intent가 smalltalk일 때만 한두 문장의 짧은 답변(공시 근거 없이), "
            "그 외에는 null\n"
            "- search_query: qa/summary/macro일 때, 이 질문을 공시 검색에 쓸 핵심 키워드로 "
            "정제하라. 다음을 모두 제거하고 '핵심 명사 1~3개'만 남겨라:\n"
            f"    · 방 회사명('{company_name}')\n"
            "    · 말투/요청어('얼마야', '알려줘', '어때', '무엇', '?')\n"
            "    · 시간/기간 표현('최근', '요즘', '올해', '작년', '3분기' 등) — 검색을 흐리므로 반드시 제거\n"
            "  예: '삼성전자 최근 영업이익이 얼마야?' → '영업이익'\n"
            "      '작년 자기주식 취득 규모 알려줘' → '자기주식 취득'\n"
            "  smalltalk/out_of_scope면 null\n"
            "- date_from / date_to: 질문에 기간 표현이 있으면 YYYYMMDD 로 환산하라. "
            "오늘 날짜는 {today} 다.\n"
            "    · '2024년' → date_from=20240101, date_to=20241231\n"
            "    · '작년' → 작년 1월 1일 ~ 12월 31일\n"
            "    · '최근 3개월' → 오늘 기준 3개월 전 ~ 오늘\n"
            "    · 기간 표현이 없으면 둘 다 null\n"
            "- prefer_recent: '최근/가장 최근/요즘/현재' 처럼 '제일 최신 것'을 원하면 true, "
            "아니면 false\n"
            "- financial_relevant: 매출·영업이익·당기순이익·자산·부채·자본 등 핵심 재무 수치를 "
            "묻는 질문이면 **단순 조회여도 무조건 true**. (예: '영업이익 얼마야?', '최근 매출은?', "
            "'순이익 알려줘', '영업이익 작년보다 얼마나 늘었어?' → 전부 true) "
            "재무 수치와 무관한 질문(사건·계약·지분 등)만 false\n"
        ),
        expected_output="RouterResult 스키마에 맞는 분류 결과",
        agent=agent,
        output_pydantic=RouterResult,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff(
        inputs={"question": question, "history_block": history_block, "today": today}
    )
    return _as_pydantic(result, RouterResult)


# ============================================================
# Title — 첫 질문을 짧은 대화 제목으로 (마이페이지 목록용)
# ============================================================
def run_title(question: str) -> str:
    """사용자 첫 질문을 15자 내외의 짧은 제목으로 요약한다 (LLM 1콜)."""
    settings = get_settings()
    agent = Agent(
        role="대화 제목 생성기",
        goal="사용자의 첫 질문을 마이페이지 목록에 쓸 짧은 제목으로 요약한다.",
        backstory="너는 핵심만 추려 15자 내외의 명사형 제목을 만든다. 군더더기·문장부호 없이.",
        llm=_llm(settings.litellm_router_model),
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=(
            f"다음 질문을 15자 내외의 짧은 제목으로 요약하라. "
            "명사형으로, 따옴표·마침표·물음표 없이 제목만 출력하라.\n"
            f"질문: {question}"
        ),
        expected_output="짧은 제목 텍스트 한 줄",
        agent=agent,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    title = (getattr(result, "raw", None) or str(result)).strip().strip('"\'""').strip()
    # 안전장치: 너무 길면 자르고, 비면 질문 앞부분으로 폴백
    if not title:
        title = question.strip()[:20]
    return title[:40]


# ============================================================
# Summary Agent
# ============================================================
def run_summary(analysis_id: str, full_text: str) -> SummaryResult:
    """retrieve-then-read 요약. 전체 조망용 본문(overview) + 인용용 근거 후보(검색)를
    함께 주고, 툴 없이 읽어서 요약한다 (gpt-5.1 툴호출 버그 회피)."""
    settings = get_settings()
    overview = full_text[:_MAX_OVERVIEW_CHARS]
    # 인용에 쓸 근거 후보 (요약은 문서 전반이라 핵심어로 폭넓게 끌어옴)
    candidates = get_vector_store().search(
        analysis_id, "핵심 주요 사항 실적 계약 일자 금액", top_k=8
    )
    context = _format_context(candidates)

    agent = Agent(
        role="공시 요약 전문가",
        goal="공시의 핵심을 정확하고 간결하게 요약한다. 수치·일자·계약상대방 같은 사실은 반드시 원문 근거로 뒷받침한다.",
        backstory="너는 금융감독원 전자공시를 오래 분석해 온 애널리스트다. 과장이나 추측 없이 사실만 요약한다.",
        llm=_llm(settings.litellm_summary_model),
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=(
            "다음 공시를 분석해 구조화된 요약을 작성하라.\n"
            "- headline: 한 줄 핵심\n"
            "- key_points: 투자자가 알아야 할 핵심 3~6개\n"
            "- summary: 3~5문장 본문 요약\n"
            "- citations: 핵심 수치/사실의 근거. 아래 '근거 후보'에서 골라 chunk_id 는 "
            "[대괄호] 안 값을 그대로, quote 는 그 문장을 그대로 옮겨라. 없으면 비워라.\n\n"
            "=== 공시 원문(일부) ===\n{overview}\n\n"
            "=== 근거 후보 ===\n{context}"
        ),
        expected_output="SummaryResult 스키마에 맞는 구조화 결과",
        agent=agent,
        output_pydantic=SummaryResult,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff(inputs={"overview": overview, "context": context})
    summary = _as_pydantic(result, SummaryResult)
    summary.citations = _reconcile(summary.citations, candidates)
    return summary


# ============================================================
# QA Agent
# ============================================================
def run_qa(
    analysis_id: str,
    question: str,
    history: list[ChatTurn] | None = None,
) -> QAResult:
    """단일 공시(analysis_id) 근거 기반 질의응답.

    retrieve-then-read 로 통일: 단일 공시 컬렉션에서 먼저 검색한 뒤 run_chat_qa 에
    위임한다 (툴호출 버그 없음, 근거 정본화 포함).
    """
    settings = get_settings()
    retrieved = get_vector_store().search(analysis_id, question, top_k=settings.top_k)
    return run_chat_qa(question, retrieved, history=history)


def run_chat_qa(
    question: str,
    retrieved: list[Citation],
    history: list[ChatTurn] | None = None,
) -> QAResult:
    """retrieve-then-read 방식 QA (핑퐁 채팅용).

    검색은 호출 측(chat 서비스)에서 미리 끝내 retrieved 로 넘기고, 여기서는 그
    근거 '안에서만' 답을 쓴다. 에이전트에 검색 툴을 주지 않으므로 툴호출 형식
    오류가 없고, 근거(chunk_id·quote)도 검색 결과에서 그대로 와서 환각이 없다.
    """
    settings = get_settings()
    history_block = _format_history(history)
    context = _format_context(retrieved)

    agent = Agent(
        role="공시 질의응답 전문가",
        goal="제공된 공시 근거에만 기반해 질문에 답한다. 근거에 없으면 모른다고 답한다.",
        backstory="너는 근거 없는 추측을 절대 하지 않는다. 주어진 근거 밖의 사실은 말하지 않는다.",
        llm=_llm(settings.litellm_qa_model),
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=(
            "{history_block}"
            "아래 '검색된 공시 근거'만 사용해 질문에 답하라.\n"
            "현재 질문: {question}\n\n"
            "=== 검색된 공시 근거 ===\n{context}\n\n"
            "규칙:\n"
            "- 질문에 '그거/그것/방금/위에서' 같은 지시 표현이 있으면 이전 대화로 해석한다.\n"
            "- answer: 위 근거에 기반한 답변. 근거로 답할 수 없으면 answerable=false 로 두고 "
            "answer 에 '공시에서 확인할 수 없음' 이라고 적어라.\n"
            "- citations: 실제로 사용한 근거만 담는다. 각 항목의 chunk_id 는 위 근거의 "
            "[대괄호] 안 값을 그대로 적고, quote 는 그 근거 문장을 그대로 옮겨라. 새로 지어내지 마라.\n"
            "- 근거 중 '재무제표 주요계정'(정형 데이터, 당기/전기 명시)이 있으면, 같은 항목은 "
            "본문 텍스트의 단편 수치보다 **재무제표 값을 우선 신뢰**하라. 재무제표에 명확한 값이 "
            "있으면 그걸로 답하고, 본문에 라벨 모호한 수치가 여럿 보여도 불필요하게 되묻지 마라.\n"
            "- 거시지표 근거('거시지표(ECOS)')가 있으면, 답변에 '같은 시점의 사실'로만 녹여라. "
            "'환율 때문에/덕분에 ~했다' 같은 인과 단정은 금지하고, 공시 근거로 뒷받침될 때만 조심스럽게 "
            "연결하라('같은 시기에 환율은 X였다' 수준).\n"
            "- answerable: 근거로 답했으면 true, 못 했으면 false.\n"
            "- needs_clarification: 근거에 답 '후보'가 여러 개인데 어느 것인지 기준이 모호해서 "
            "(예: 당분기 vs 누적, 연결 vs 별도, 사업부문 구분, 단위) 함부로 답하면 틀릴 수 있으면 true. "
            "이때 answer 에는 사용자에게 '되묻는 질문'을 쓰고, 보이는 후보 값을 함께 제시하라 "
            "(예: '당분기 기준인가요, 누적 기준인가요? 현재 보이는 값: 당분기 2,220 / 누적 536,633'). "
            "기준이 명확하거나 상식적으로 하나로 정해지면 false 로 두고 그냥 답하라. "
            "사소한 모호함까지 매번 되묻지는 마라(꼭 필요할 때만)."
        ),
        expected_output="QAResult 스키마에 맞는 구조화 결과",
        agent=agent,
        output_pydantic=QAResult,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff(
        inputs={"question": question, "history_block": history_block, "context": context}
    )
    qa = _as_pydantic(result, QAResult)
    if not qa.question:
        qa.question = question
    # 근거 정본화: LLM 이 고른 chunk_id 를 retrieved 의 정본 Citation 으로 환원
    # (quote 환각 차단 + '[id]' 괄호/중복 정리)
    qa.citations = _reconcile(qa.citations, retrieved)
    return qa


def _normalize_chunk_id(cid: str) -> str:
    """LLM 이 '[id]', 공백 등을 붙여 반환하는 경우를 정리한다."""
    return (cid or "").strip().strip("[]").strip()


def _reconcile(picked: list[Citation], retrieved: list[Citation]) -> list[Citation]:
    """LLM 이 고른 citation 을 검색 결과의 정본으로 환원 (없는 id 는 버림, 중복 제거)."""
    by_id = {c.chunk_id: c for c in retrieved}
    seen: set[str] = set()
    out: list[Citation] = []
    for c in picked:
        cid = _normalize_chunk_id(c.chunk_id)
        if cid in by_id and cid not in seen:
            seen.add(cid)
            out.append(by_id[cid])
    return out


def _format_context(citations: list[Citation]) -> str:
    if not citations:
        return "(검색 결과 없음)"
    lines = []
    for c in citations:
        src = " ".join(x for x in [c.report_nm, c.rcept_dt] if x) or "-"
        lines.append(f"[{c.chunk_id}] (출처: {src} / {c.section_title or '-'}) {c.quote}")
    return "\n".join(lines)


def _format_history(history: list[ChatTurn] | None) -> str:
    if not history:
        return ""
    lines = []
    for t in history:
        who = "사용자" if t.role == "user" else "AI"
        lines.append(f"{who}: {t.content}")
    return "=== 이전 대화 ===\n" + "\n".join(lines) + "\n\n"


# 거시(Macro)는 별도 에이전트로 두지 않는다. 거시 데이터(ECOS)는 코드로 가져와
# QA 근거에 합쳐 한 번에 통합 답변하게 한다 (chat 서비스 참고). 객관성 가드는 QA 프롬프트에 내장.


# ============================================================
# Verification Agent (Opus) — Double LLM Orchestration
# ============================================================
def run_verification(
    target: str,
    answer: str,
    citations: list[Citation],
    golden_answer: str | None = None,
) -> VerificationResult:
    settings = get_settings()
    cite_text = "\n".join(
        f"[{c.chunk_id}] ({c.section_title or '-'}) {c.quote}" for c in citations
    ) or "(제공된 근거 없음)"
    golden_block = (
        f"\n\n=== 골든셋 정답(참고) ===\n{golden_answer}" if golden_answer else ""
    )

    agent = Agent(
        role="공시 답변 검증관",
        goal="답변이 제공된 근거에 충실한지 엄격히 검증한다. 근거를 벗어난 주장(환각)을 잡아낸다.",
        backstory="너는 깐깐한 감사관이다. 근거에 명시되지 않은 내용은 모두 문제로 표시한다.",
        llm=_llm(settings.litellm_verification_model),
        verbose=False,
        allow_delegation=False,
    )
    task = Task(
        description=(
            "아래 답변이 근거에 충실한지 검증하라.\n\n"
            f"=== 검증 대상 ===\n{target}\n\n"
            f"=== 답변 ===\n{answer}\n\n"
            f"=== 근거 ===\n{cite_text}{golden_block}\n\n"
            "판정 기준:\n"
            "- grounded_score: 0~1 근거 충실도. **근거에 없는 주장(인과 단정·해석·추측·"
            "미근거 수치)이 하나라도 섞이면 0.5 이하로 매겨라.** 핵심은 맞아도 일부가 근거 없으면 "
            "'완전 부합'이 아니다. 모든 핵심·디테일이 근거로 뒷받침될 때만 0.7 이상.\n"
            "- verdict: grounded_score 기준 (0.7↑ pass / 0.4~0.7 partial / 0.4↓ fail). "
            "최종 verdict 는 시스템이 점수로 확정하니, 점수를 정확히 매기는 데 집중하라.\n"
            "- reason: 판정 이유 (특히 근거 없는 주장이 있으면 명시)\n"
            "- issues: 근거를 벗어난 구체적 문제점 목록 (없으면 빈 배열)\n"
            "골든셋 정답이 주어졌다면 답변이 그와 사실적으로 일치하는지도 함께 평가하라."
        ),
        expected_output="VerificationResult 스키마에 맞는 구조화 결과",
        agent=agent,
        output_pydantic=VerificationResult,
    )
    crew = Crew(agents=[agent], tasks=[task], process=Process.sequential, verbose=False)
    result = crew.kickoff()
    vr = _as_pydantic(result, VerificationResult)
    if not vr.target:
        vr.target = target
    # 정성·정량 모순 방지: verdict 를 grounded_score 로 코드가 확정 (단일 기준)
    vr.verdict = _verdict_from_score(vr.grounded_score)
    return vr


def _verdict_from_score(score: float) -> VerificationVerdict:
    """grounded_score → verdict 매핑 (임계값은 config). 0.5는 항상 partial."""
    s = get_settings()
    if score >= s.verify_pass_min:
        return VerificationVerdict.PASS
    if score >= s.verify_partial_min:
        return VerificationVerdict.PARTIAL
    return VerificationVerdict.FAIL


# ============================================================
# 헬퍼
# ============================================================
def _as_pydantic(crew_output, model_cls):
    """CrewOutput 에서 pydantic 결과를 안전하게 꺼낸다."""
    obj = getattr(crew_output, "pydantic", None)
    if isinstance(obj, model_cls):
        return obj
    # fallback: dict 로 받았을 경우
    data = getattr(crew_output, "json_dict", None)
    if isinstance(data, dict):
        return model_cls.model_validate(data)
    raise ValueError(f"{model_cls.__name__} 결과 파싱 실패: {crew_output!r}")
