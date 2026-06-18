"""골든셋 평가 실행기.

골든셋(공시 + 질문 + 정답)을 파이프라인에 돌려, Verification Agent(Opus)가
각 답변의 근거 충실도와 골든 정답 일치 여부를 판정한 결과를 집계한다.

실행:
    python -m eval.run_golden eval/golden_set.example.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from app.schemas.disclosure import AnalyzeRequest, DisclosureSource
from app.services import pipeline


def run(path: str) -> None:
    cases = json.loads(Path(path).read_text(encoding="utf-8"))
    total = passed = 0

    for case in cases:
        qa_pairs = case.get("qa", [])
        questions = [q["question"] for q in qa_pairs]
        golden = {q["question"]: q["golden_answer"] for q in qa_pairs}

        req = AnalyzeRequest(
            source=DisclosureSource.TEXT,
            raw_text=case["raw_text"],
            company_name=case.get("company_name"),
            title=case.get("title"),
            questions=questions,
        )
        result = pipeline.analyze(req, golden_answers=golden)

        print(f"\n=== {case.get('company_name')} / {case.get('title')} ===")
        print(f"상태: {result.status.value}")
        if result.error:
            print("오류:", result.error)
            continue

        for v in result.verifications:
            total += 1
            ok = v.verdict.value == "pass"
            passed += int(ok)
            mark = "✅" if ok else "❌"
            print(f"  {mark} [{v.verdict.value}] grounded={v.grounded_score:.2f} :: {v.target}")
            if v.issues:
                print("      issues:", "; ".join(v.issues))

    if total:
        print(f"\n총 {total}건 중 pass {passed}건 ({passed / total:.0%})")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "eval/golden_set.example.json"
    run(target)
