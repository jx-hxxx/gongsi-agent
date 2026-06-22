"""3-트랙 챗 골든셋 실행기 — POST /api/v1/chat 회귀 검증.

요약 개선(서술 프롬프트·summary_top_k·선택적 빌드·숫자=RAG 분리)이 의도대로
동작하는지, 케이스별 기대(expect)를 실제 응답과 대조해 통과율을 집계한다.

전제: FastAPI 서버가 떠 있어야 한다.
    uvicorn app.main:app --port 8000

실행 (프로젝트 루트):
    python -m eval.run_chat_golden                       # 기본 localhost:8000, eval/chat_golden_set.json
    python -m eval.run_chat_golden --base http://localhost:8001
    python -m eval.run_chat_golden --set eval/chat_golden_set.json

판정:
    - severity=must  : 기대 불충족 시 FAIL (집계의 합격 기준)
    - severity=watch : 기대 불충족 시 WATCH (알려진 약점, 합격률에서 분리 집계)
종료코드: must 케이스가 모두 통과면 0, 아니면 1 (CI 연동용)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

NUM_RE = re.compile(r"\d{1,3}(?:,\d{3})+")


def call_chat(base: str, case: dict, timeout: float = 120.0) -> dict:
    body = {
        "roomId": 1,
        "userSeq": 1,
        "companyContext": case["company"],
        "messages": [{"role": "user", "content": case["question"]}],
    }
    req = urllib.request.Request(
        base.rstrip("/") + "/api/v1/chat",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def evaluate(case: dict, r: dict) -> tuple[bool, list[str]]:
    """기대(expect)와 응답(r)을 대조. (통과여부, 실패사유목록) 반환."""
    exp = case.get("expect", {})
    reasons: list[str] = []
    text = " ".join(
        str(r.get(k) or "") for k in ("answerText", "sourceContent", "macroSnapshot")
    )

    if "intent" in exp and r.get("intent") != exp["intent"]:
        reasons.append(f"intent={r.get('intent')} (기대 {exp['intent']})")
    if exp.get("out_of_scope") is True and not r.get("outOfScope"):
        reasons.append(f"outOfScope={r.get('outOfScope')} (기대 true)")
    if "verdict_not" in exp:
        v = (r.get("verification") or {}).get("verdict")
        if v == exp["verdict_not"]:
            reasons.append(f"verdict={v} (금지)")
    if "contains_any" in exp:
        if not any(s in text for s in exp["contains_any"]):
            reasons.append(f"contains_any 미충족 {exp['contains_any']}")
    if exp.get("has_number") and not NUM_RE.search(text):
        reasons.append("숫자 토큰 없음")
    if r.get("error"):
        reasons.append(f"error={r['error'].get('code')}")
    return (not reasons), reasons


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://localhost:8000")
    ap.add_argument("--set", default=str(Path(__file__).with_name("chat_golden_set.json")))
    args = ap.parse_args()

    data = json.loads(Path(args.set).read_text(encoding="utf-8"))
    cases = data["cases"]

    must_total = must_pass = watch_total = watch_pass = 0
    print(f"=== 챗 골든셋 {len(cases)}케이스 @ {args.base} ===\n")
    for c in cases:
        sev = c.get("severity", "must")
        try:
            r = call_chat(args.base, c)
        except urllib.error.URLError as e:
            print(f"  [ERR ] {c['id']}: 서버 호출 실패 — {e}. 서버가 떠 있는지 확인.")
            return 2
        ok, reasons = evaluate(c, r)
        if sev == "must":
            must_total += 1
            must_pass += ok
        else:
            watch_total += 1
            watch_pass += ok
        tag = ("PASS" if ok else ("FAIL" if sev == "must" else "WATCH"))
        line = f"  [{tag:5}] {c['id']:26} | {c['behavior']}"
        if not ok:
            line += f"\n          ↳ {'; '.join(reasons)}"
        print(line)

    print(f"\n--- 결과 ---")
    print(f"must  : {must_pass}/{must_total} 통과")
    print(f"watch : {watch_pass}/{watch_total} 통과 (알려진 약점, 합격 기준 제외)")
    ok_all = must_pass == must_total
    print("\n" + ("✅ must 전부 통과" if ok_all else "❌ must 미통과 있음"))
    return 0 if ok_all else 1


if __name__ == "__main__":
    sys.exit(main())
