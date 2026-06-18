# 공시분석 AI Chat API 명세서 (FastAPI)

| 항목 | 내용 |
|---|---|
| 서비스 | 공시분석 AI (CrewAI + FastAPI) |
| 작성 | AI 파트 (지현) |
| 버전 | v2.0 (백엔드 연동 명세서 v2.0 정합) |
| 엔드포인트 | `POST /api/v1/chat` (동기 JSON) |
| 기계판독 스키마 | `docs/chat_v2_schema.json` (JSON Schema) |

---

## 1. 개요

- **역할**: Stateless RAG + 검증 추론. (질문 + 대화이력 + 기업)을 받아 **답변·출처·검증·분기신호를 단일 JSON으로** 반환.
- **무상태(stateless)**: 세션/이력을 보관하지 않음. 매 요청에 전체 `messages` 동봉.
- **동기 호출**: SSE 미사용. 내부에서 라우팅·검색·QA·검증을 모두 끝낸 뒤 한 번에 응답.
- **호출자**: Spring Boot 백엔드 (사설망, 앱 인증 없음, 기본 포트 8001).

---

## 2. 엔드포인트

| 항목 | 값 |
|---|---|
| Method / Path | `POST /api/v1/chat` |
| 요청 Content-Type | `application/json; charset=utf-8` |
| 응답 Content-Type | `application/json; charset=utf-8` |
| 요청 헤더 | `X-Trace-Id`(UUID, 권장) — 응답에 동일 echo |

> 보조 엔드포인트: `POST /api/v1/chat/title` (첫 질문 → 대화 제목 요약). 핵심 계약 외 선택 기능. (요청 `{ "question": "..." }` → 응답 `{ "title": "..." }`)

---

## 3. 요청 (ChatV2Request)

| 필드 | 타입 | 필수 | 설명 |
|---|---|---|---|
| `roomId` | integer | ✅ | 대화방 ID. 로그·응답 echo 용 |
| `userSeq` | integer | ✅ | 사용자 시퀀스. 로그 전용 |
| `companyContext` | object | ✅ | 대화 대상 기업 (한 방 = 한 기업) |
| `companyContext.corpCode` | string | ✅ | DART 고유번호(8자리). RAG 검색 범위 키 (`corpus_<corpCode>`) |
| `companyContext.corpName` | string | ✅ | 기업명 |
| `messages` | array | ✅ | 멀티턴. 시간순, **마지막 항목은 `role:user`(=현재 질문)** |
| `messages[].role` | enum | ✅ | `user` \| `assistant` |
| `messages[].content` | string | ✅ | 메시지 본문 |

> AI는 `messages` 중 **최근 10개 메시지만** 단기 메모리로 사용(토큰 안전장치). 마지막 user 메시지는 현재 질문, 나머지는 history.

### 요청 예시
```json
{
  "roomId": 142,
  "userSeq": 12345,
  "companyContext": { "corpCode": "00126380", "corpName": "삼성전자" },
  "messages": [
    {"role": "user", "content": "영업이익 알려줘"},
    {"role": "assistant", "content": "2025년 영업이익은 ..."},
    {"role": "user", "content": "그럼 작년보다 얼마나 늘었어?"}
  ]
}
```

---

## 4. 응답 (ChatV2Response)

모든 필드가 키로 존재(값은 `null` 또는 채움). HTTP는 **추론 실패도 200** + `error` 필드, 시스템 실패만 4xx/5xx.

| 필드 | 타입 | 설명 |
|---|---|---|
| `roomId` | integer | 요청 echo |
| `intent` | string\|null | `qa` \| `summary` \| `macro` \| `smalltalk` \| `out_of_scope` |
| `answerText` | string\|null | 답변 본문. 완전 실패 시 `null` |
| `sourceContent` | string\|null | 인용 근거 텍스트(§5 포맷). 근거 없으면 `null` |
| `macroSnapshot` | string\|null | 거시지표 텍스트(환율·금리·국고채·KOSPI). 미사용 시 `null` |
| `sources` | array\|null | 구조화 출처(프론트 카드용). 근거 없으면 `null` |
| `sources[].rceptNo` | string | 공시 접수번호 |
| `sources[].reportNm` | string | 공시명 |
| `sources[].rceptDt` | string | 접수일 `YYYYMMDD` |
| `sources[].sectionTitle` | string | 인용 섹션 |
| `sources[].quote` | string | 인용 본문 |
| `sources[].score` | number | 유사도 0~1 |
| `outOfScope` | boolean | true → 다른 회사 질문(프론트 팝업) |
| `detectedCompany` | string\|null | outOfScope 시 감지된 회사 |
| `needsClarification` | boolean | true → `answerText`가 되묻는 질문 |
| `verification` | object\|null | 검증 결과 (smalltalk/out_of_scope/실패 시 `null`) |
| `verification.verdict` | string | `pass` \| `partial` \| `fail` |
| `verification.groundedScore` | number | 근거 충실도 0~1 |
| `error` | object\|null | 완전 실패 시 채움 (그 외 `null`) |
| `error.code` | string | §6 에러코드 |
| `error.message` | string | 설명 |
| `error.retriable` | boolean | 재시도 가능 여부 |

### 4.1 정상 (qa, 인용 포함)
```json
{
  "roomId": 142,
  "intent": "qa",
  "answerText": "연결재무제표 기준 영업이익은 전기 32.7조 → 당기 43.6조로 10.875조 증가했습니다.",
  "sourceContent": "[20260310002820 / 삼성전자 2025 사업보고서 재무제표]\n영업이익 — 당기 43,601,051,000,000원, 전기 32,725,961,000,000원",
  "macroSnapshot": null,
  "sources": [
    {"rceptNo":"20260310002820","reportNm":"2025 사업보고서 재무제표","rceptDt":"20260310",
     "sectionTitle":"재무제표 주요계정(연결재무제표)","quote":"영업이익 — 당기 43,601,051,000,000원, 전기 32,725,961,000,000원","score":1.0}
  ],
  "outOfScope": false,
  "detectedCompany": null,
  "needsClarification": false,
  "verification": {"verdict":"pass","groundedScore":1.0},
  "error": null
}
```

### 4.2 다른 회사 (out_of_scope)
```json
{
  "roomId": 142, "intent": "out_of_scope",
  "answerText": "이 방은 '삼성전자' 전용입니다. '현대차' 관련 질문은 해당 상세페이지에서 해주세요.",
  "sourceContent": null, "macroSnapshot": null, "sources": null,
  "outOfScope": true, "detectedCompany": "현대차",
  "needsClarification": false, "verification": null, "error": null
}
```

### 4.3 되묻기 (needsClarification)
```json
{
  "roomId": 142, "intent": "qa",
  "answerText": "어느 기준의 영업이익인가요? 당분기 실적 vs 누적 중 알려주시면 정확히 답해 드릴게요.",
  "sourceContent": null, "macroSnapshot": null, "sources": null,
  "outOfScope": false, "detectedCompany": null,
  "needsClarification": true, "verification": null, "error": null
}
```

### 4.4 추론 실패 (error)
```json
{
  "roomId": 142, "intent": "qa",
  "answerText": null, "sourceContent": null, "macroSnapshot": null, "sources": null,
  "outOfScope": false, "detectedCompany": null, "needsClarification": false,
  "verification": null,
  "error": {"code":"LLM_TIMEOUT","message":"LLM 응답이 60초 내 도착하지 않았습니다","retriable":true}
}
```

---

## 5. `sourceContent` 포맷

```
[<rceptNo> / <corpName> <reportNm>]
<인용 본문>

[<rceptNo> / <corpName> <reportNm>]
<인용 본문>
```
- 청크 간 `\n\n` 구분. `sources[]`와 동일 내용의 텍스트 버전(DB 저장용).

---

## 6. 값 정의

### 6.1 intent
| 값 | 의미 | 출처/검증 |
|---|---|---|
| `qa` | 사실·수치 질문 | 출처 ✅ / 검증 ✅ |
| `summary` | 요약·동향 | 출처 ✅ / 검증 ✅ |
| `macro` | 거시 관련 | 출처 ✅ / 검증 ✅ / macroSnapshot |
| `smalltalk` | 인사·잡담 | 없음 |
| `out_of_scope` | 다른 회사 | 없음(팝업) |

### 6.2 verification.verdict (groundedScore 기준 — 시스템이 점수로 확정)
| groundedScore | verdict |
|---|---|
| ≥ 0.7 | `pass` |
| 0.4 ~ 0.7 | `partial` |
| < 0.4 | `fail` (CRAG 재검색 1회 후에도 실패 시. 답은 반환하되 신뢰도 낮음 표기) |

### 6.3 error.code
| code | 상황 | retriable |
|---|---|---|
| `EMBEDDING_FAILED` | 임베딩 호출 실패 | true |
| `VECTOR_SEARCH_FAILED` | 벡터DB 검색 실패 | true |
| `CONTEXT_OVERFLOW` | 컨텍스트 한계 초과 | false |
| `LLM_TIMEOUT` | LLM 60초 초과 | true |
| `LLM_API_ERROR` | LLM 5xx | true |
| `RATE_LIMITED` | provider rate limit | true |
| `INVALID_REQUEST` | 요청 스키마 오류(HTTP 400) | false |
| `CONTENT_FILTERED` | 안전 필터 거부 | false |
| `INTERNAL_ERROR` | 미분류 예외 | true |

> **부분 실패**(검증/거시/재무 sub-step 실패하지만 답변은 정상)는 `error`로 올리지 않고 로그만 남김. `error`는 **답 자체를 못 만든 경우**에만.

### 6.4 HTTP status
| status | 상황 |
|---|---|
| `200` | 정상 + 추론실패(body.error) 모두 |
| `400` | 요청 스키마 오류 (messages 마지막이 user 아님 등) |
| `503` | 서비스 다운 |
| `504` | 게이트웨이 타임아웃 |

---

## 7. 분기 처리 (호출 측)

| 응답 조건 | 처리 |
|---|---|
| `outOfScope=true` | `answerText`를 팝업으로, `detectedCompany` 페이지 이동 버튼(선택) |
| `needsClarification=true` | `answerText`(되묻는 질문) 표시 → 사용자 답을 다음 턴 `messages`에 포함 |
| `sources` 있음 | 답변 아래 출처 카드 |
| `macroSnapshot` 있음 | 시장 맥락 표시(선택) |
| `verification.verdict` | 신뢰도 뱃지(선택) |
| `error` 있음 | 코드별 안내, `retriable`이면 1회 재시도 권장 |

---

## 8. 데이터 스키마 (기계판독)

- 정식 JSON Schema: **`docs/chat_v2_schema.json`** (Pydantic 모델에서 자동 생성)
- 실시간 OpenAPI: 서버 기동 시 `GET /openapi.json`, 문서 UI `GET /docs`

---

## 9. 부록 — 내부 처리 흐름 (참고)
```
Router(gpt-5.1: 의도·스코프·거시/재무·검색어·날짜)
 → out_of_scope/smalltalk 즉시 반환
 → qa/summary/macro: 코퍼스 검색(+재무 DART, +거시 ECOS) → QA(gpt-5.1) 통합 답변
    → Verification(o4-mini) 채점 → fail이면 CRAG 재검색 1회
 → 응답 조립
```
- 임베딩 `text-embedding-3-small`, 벡터DB Chroma(회사별 `corpus_<corpCode>`)
- 적재: 삼성전자(00126380)·현대자동차(00164742) 3개년 공시
