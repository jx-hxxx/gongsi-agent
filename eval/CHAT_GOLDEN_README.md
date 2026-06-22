# 3-트랙 챗 골든셋 (회귀 검증)

> 목적: 요약 기능 개선("숫자는 RAG, 요약은 서술")이 **의도대로 동작하는지 자동 검증**하고, 이후 변경에서 **회귀를 잡는다.**
> 대상: `POST /api/v1/chat` (챗 3-트랙). 단일-공시 분석 파이프라인용 [`run_golden.py`](run_golden.py)와는 별개다.
> 파일: 케이스 [`chat_golden_set.json`](chat_golden_set.json) · 실행기 [`run_chat_golden.py`](run_chat_golden.py)
> 관련 성과: [../docs/요약기능_개선_성과보고서.md](../docs/요약기능_개선_성과보고서.md)

---

## 1. 왜 이 골든셋인가

요약 개선으로 바꾼 동작(아래)을 **수동 E2E가 아니라 반복 가능한 케이스로 고정**하기 위함이다. 기존 골든셋(`run_golden.py` + `golden_set.json`)은 *단일 공시 분석 파이프라인(`pipeline.analyze`)* 을 평가하므로, 우리가 바꾼 **챗 3-트랙(요약 트랙·precompute·라우팅)** 은 다루지 못한다 → 별도 셋이 필요했다.

### 이 골든셋이 지키는 변경 사항
| 변경 | 검증 케이스 |
|---|---|
| 서술 중심 요약 프롬프트(메타설명 금지) | `*-summary-*` (개요·사업 내용 서술이 나오는지) |
| "숫자는 RAG"로 위임 | `*-num-*` (숫자 질문이 qa→RAG로 정확값) |
| Router 라우팅 견고성 | `*-route-business-plain` ('요약' 단어 없는 서술 질문) |
| 스코프 가드 | `*-scope-other-company` (다른 회사 질문 차단) |

## 2. 케이스 매트릭스 (회사당 7 × 2사 = 14)

| 동작(behavior) | 삼성전자 | 현대자동차 | 판정 |
|---|---|---|---|
| 요약: 회사 개요(서술) | ss-summary-overview | hd-summary-overview | must |
| 요약: 사업 내용(서술) | ss-summary-business | hd-summary-business | must |
| 숫자: 매출(RAG) | ss-num-revenue | hd-num-revenue | must |
| 숫자: 영업이익(RAG) | ss-num-opincome | hd-num-opincome | must |
| 숫자: 자산/부채(RAG) | ss-num-assets | hd-num-liabilities | must |
| 라우팅: '요약' 단어 없는 서술 | ss-route-business-plain | hd-route-business-plain | **watch** |
| 스코프: 다른 회사 차단 | ss-scope-other-company | hd-scope-other-company | must |

> 개수 근거: "마법의 숫자"가 아니라 **바꾼 동작(7행)마다 회사별 1케이스**로 커버. 통계적 표본이 아니라 *행동 핀(behavior pin)* 이 목적이므로 소표본으로 충분하다.

## 3. 판정 기준

각 케이스 `expect`를 응답과 대조(`run_chat_golden.py:evaluate`):
- `intent` — 기대 intent 일치 (예: summary / qa)
- `contains_any` — `answerText`·`sourceContent`에 하나 이상 포함 (서술=세그먼트 키워드, 숫자=콤마 구분 핵심값)
- `has_number` — 콤마 구분 숫자 토큰 존재 (정확값 미고정 케이스용)
- `verdict_not` — 이 verdict면 실패 (보통 `fail`)
- `out_of_scope` — `outOfScope` 기대값
- `error` 필드가 채워지면 무조건 실패

**severity**
- `must` — 합격 기준. 전부 통과해야 종료코드 0.
- `watch` — *알려진 약점* 추적용(미통과 허용, 합격률과 분리 집계). 현재 "'요약' 단어 없는 서술 질문"이 검색 랭킹 한계로 흔들릴 수 있어 watch로 둔다.

**exact vs loose (숫자)**
- 관측된 값은 exact(`contains_any`에 콤마 구분 핵심값). 미관측 값(현대 영업이익/부채)은 loose(`has_number` + 비실패).

## 4. 실행 방법

```bash
# 1) 서버 기동 (요약 데이터가 적재된 상태)
uvicorn app.main:app --port 8000

# 2) 골든셋 실행 (프로젝트 루트)
python -m eval.run_chat_golden
#   --base http://localhost:8001   # 포트 다르면
#   --set  eval/chat_golden_set.json
```
- 비용: 케이스당 챗 1턴(Router+QA gpt-5.1 + 검증 o4-mini). 14케이스 ≈ 수 분 · ~$0.6(추정).
- 종료코드: must 전부 통과 0 / 미통과 1 / 서버 호출 실패 2 → CI 게이트로 사용 가능.

## 5. 결과 해석

```
[PASS ] ss-num-revenue            | 숫자 트랙 - 매출(RAG exact-match)
[WATCH] ss-route-business-plain   | 라우팅 견고성 - '요약' 단어 없는 서술 질문
          ↳ verdict=fail (금지)
...
must  : 12/12 통과
watch :  1/2 통과 (알려진 약점, 합격 기준 제외)
```
- `must` 합격률이 핵심. `watch` 미통과는 *개선 백로그*(검색 랭킹·라우팅)이지 회귀가 아니다.
- `FAIL`이 must에서 나면 회귀 신호 → 변경 재검토.

## 6. 한계 · 주의

- **소표본**: 동작 커버리지용이지 품질 점수가 아니다. 정밀 측정은 케이스 확장 필요.
- **숫자 기대값은 시점 의존**: 2024~2025 공시(제56/57기) 기준. 새 사업보고서가 적재되면 값 갱신 필요.
- **요약 데이터 의존**: 정기보고서 사전요약이 적재돼 있어야 요약 트랙이 사전요약을 쓴다(미적재 시 RAG 폴백으로 동작은 함).
- LLM 비결정성으로 `contains_any`가 가끔 흔들릴 수 있음 → 핵심값은 복수 후보로 둠.

## 7. 갱신 가이드

- 새 동작/트랙을 추가하면 **케이스도 추가**(behavior 1행 = 회사별 1케이스 원칙).
- 숫자 정확값은 관측 후 exact로 승격(`has_number` → `contains_any`).
- watch 케이스가 안정적으로 통과하기 시작하면 must로 승격.
