"""B단계 검증: 단건 분석 전체 파이프라인 + 5개 합격기준 체크.

실제 공시 → 요약/QA/검증 → 저장 → 조회까지 돌리고, RFP 기준으로 자동 채점.

사용:
    python verify_single.py 20251030000502
"""
from __future__ import annotations

import re
import sys

from app.data import dart
from app.schemas.disclosure import AnalyzeRequest, DisclosureSource
from app.services import pipeline
from app.storage import db


def _norm(s: str) -> str:
    return re.sub(r"\s+", "", s)


def main(rcept_no: str) -> None:
    db.init_db()
    questions = ["자기주식 처분 목적은 무엇인가?", "처분 대상 주식의 종류와 수량은?"]

    print(f"▶ 분석 시작 (rcept_no={rcept_no}) — gpt-5.1 요약/QA, o4-mini 검증\n")
    req = AnalyzeRequest(
        source=DisclosureSource.DART,
        rcept_no=rcept_no,
        company_name="삼성전자",
        title="자기주식 처분 결정",
        questions=questions,
    )
    result = pipeline.analyze(req)

    # ===== 출력 =====
    print(f"[status] {result.status.value}")
    if result.error:
        print(f"[error] {result.error}")
    if result.summary:
        print(f"\n[요약] {result.summary.headline}")
        for kp in result.summary.key_points:
            print(f"  - {kp}")
    for qa in result.qa:
        print(f"\n[Q] {qa.question}\n[A] {qa.answer}")
        for c in qa.citations:
            print(f"    근거[{c.chunk_id}] {c.quote[:70].strip()}...")
    for v in result.verifications:
        print(f"\n[검증:{v.target[:20]}] {v.verdict.value} (score={v.grounded_score}) — {v.reason[:80]}")

    # ===== 5개 합격기준 자동 채점 =====
    print("\n" + "=" * 50)
    print("합격기준 채점")
    src = _norm(dart.fetch_document_text(rcept_no))

    c1 = result.status.value == "done"
    print(f"  1.완주              : {'✅' if c1 else '❌'} ({result.status.value})")

    c2 = bool(result.summary and result.summary.headline)
    print(f"  2.요약 생성          : {'✅' if c2 else '❌'}")

    all_cites = [c for qa in result.qa for c in qa.citations]
    if result.summary:
        all_cites += result.summary.citations
    in_src = sum(1 for c in all_cites if _norm(c.quote)[:40] and _norm(c.quote)[:40] in src)
    c3 = all_cites and in_src == len(all_cites)
    print(f"  3.인용이 원문에 존재  : {'✅' if c3 else '⚠️'} ({in_src}/{len(all_cites)}건 일치)")

    c4 = bool(result.verifications) and all(
        v.verdict.value in ("pass", "partial", "fail") for v in result.verifications
    )
    print(f"  4.검증 작동          : {'✅' if c4 else '❌'} ({len(result.verifications)}건)")

    reloaded = db.get_analysis(result.analysis_id)
    c5 = reloaded is not None and reloaded.analysis_id == result.analysis_id
    print(f"  5.저장·조회          : {'✅' if c5 else '❌'} (analysis_id={result.analysis_id})")

    passed = sum([bool(c1), bool(c2), bool(c3), bool(c4), bool(c5)])
    print(f"\n  → {passed}/5 통과")


if __name__ == "__main__":
    rcept_no = sys.argv[1] if len(sys.argv) > 1 else "20251030000502"
    main(rcept_no)
