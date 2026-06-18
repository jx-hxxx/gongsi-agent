# DB 명세서 — 공시분석 AI (FastAPI)

| 항목 | 내용 |
|---|---|
| 작성 | AI 파트 (지현) |
| 범위 | AI가 소유한 저장소 (벡터DB + 캐시). 대화·이력 DB는 백엔드 소유(§4 참고) |

---

## 0. 소유권 구분 (중요)

| 저장소 | 소유 | 용도 |
|---|---|---|
| **Chroma 벡터DB** | **AI** | 공시 코퍼스(청크+임베딩) — RAG 검색 |
| **SQLite (캐시)** | **AI** | 거시지표 캐시 (+ 레거시 단건분석 테이블) |
| **대화·세션·이력 DB** | **백엔드** | tb_user / 대화방 / tb_qa_history 등 (AI는 stateless라 미보유) |

→ AI는 **대화 내용을 저장하지 않는다.** 채팅 이력 영속화는 백엔드 책임. §4에 "백엔드가 우리 응답에서 뭘 저장하면 되는지" 제안만 둠.

---

## 1. Chroma 벡터DB (AI 핵심 자산)

- 위치: `./data/chroma` (PersistentClient)
- 임베딩 모델: **OpenAI `text-embedding-3-small`**, **1536차원**
- 거리 점수: 검색 시 `score = 1 − distance` (0~1, 클수록 유사)

### 1.1 컬렉션 규칙
| 컬렉션 | 설명 |
|---|---|
| `corpus_<corpCode>` | **기업별 공시 코퍼스** (운영용). 검색은 이 컬렉션으로 회사 범위 한정 |
| `disc_<analysisId>` | (레거시) 단건 공시 분석용. 챗 경로에선 미사용 |

현재 적재 현황:
| 컬렉션 | 기업 | 청크 수 |
|---|---|---|
| `corpus_00126380` | 삼성전자 | 60,119 |
| `corpus_00164742` | 현대자동차 | 77,505 |

> 적재 범위: 각 기업 **최근 3개년** 공시 (정기/주요사항/발행/거래소/외부감사 + 선별된 지분공시). 청킹 800자/overlap 120 (조정 가능).

### 1.2 청크(레코드) 스키마
| 항목 | 내용 | 예시 |
|---|---|---|
| `id` | `<rceptNo>-c-<순번>` | `20260608800918-c-0000` |
| `document` | 청크 본문 텍스트 | "영업이익 — 당기 …" |
| `embedding` | 1536-dim float 벡터 | `[0.012, -0.08, …]` |
| **metadata** | (아래 표) | |

#### metadata 필드
| 키 | 타입 | 설명 |
|---|---|---|
| `corp_name` | string | 기업명 (예: 삼성전자) |
| `rcept_no` | string | 공시 접수번호 (출처 추적 키) |
| `rcept_dt` | string | 접수일 `YYYYMMDD` (날짜 필터·최신 정렬용) |
| `report_nm` | string | 공시명 (예: 사업보고서) |
| `pblntf_ty` | string | 공시유형 코드 (A정기·B주요사항·C발행·D지분·F외부감사·I거래소) |
| `section_title` | string | 청크가 속한 섹션 제목 |
| `order` | integer | 문서 내 청크 순번 |

→ 응답의 `sources[]`(rceptNo·reportNm·rceptDt·sectionTitle·quote·score)는 이 메타데이터에서 생성된다.

---

## 2. SQLite (AI 캐시·레거시)

- 위치: `./data/app.db`

### 2.1 `macro_cache` (활성 — 거시지표 캐시)
| 컬럼 | 타입 | 설명 |
|---|---|---|
| `as_of` | TEXT (PK) | 기준일 `YYYYMMDD` |
| `payload` | TEXT(JSON) | 그 날짜 거시 스냅샷(환율·기준금리·국고채3년·KOSPI) |

> 과거 거시값은 불변 → 날짜당 1회만 ECOS 호출하고 캐시(lazy-fill). 공시 발생일 기준으로 조회.

### 2.2 레거시 테이블 (단건분석 경로 — 챗 경로 미사용)
| 테이블 | 컬럼 |
|---|---|
| `analyses` | analysis_id(PK), company_name, title, headline, status, created_at, payload(JSON) |
| `chat_turns` | id(PK), analysis_id, role, content, created_at |

> 초기 "공시 1건 분석" 기능용. 현재 핑퐁 챗(`/api/v1/chat`)은 stateless라 이 테이블에 쓰지 않음. 제거 가능하나 호환 위해 유지.

---

## 3. 데이터 흐름 요약
```
[사전 적재] DART 공시 원문 → 청킹 → text-embedding-3-small → Chroma(corpus_<corpCode>)
[질의 시]   질문 → 임베딩 → Chroma 검색(회사 한정) → 근거 → LLM 답변
[거시]      공시일 기준 ECOS 조회 → macro_cache 저장/재사용
[대화이력]  AI 미저장 → 백엔드가 응답 받아 자기 DB에 영속화
```

---

## 4. (제안) 백엔드 대화 이력 DB — 우리 응답 → 저장 매핑

대화 이력 테이블은 **백엔드 소유**이며 설계도 백엔드 몫이다. 다만 AI 응답에서 **저장할 가치가 있는 필드**를 제안한다 (예: `tb_qa_history`).

| 저장 권장 컬럼 | 출처(AI 응답 필드) | 비고 |
|---|---|---|
| `room_id` | `roomId` | 대화방 |
| `role` / `content` | 사용자 질문 / `answerText` | 턴 본문 |
| `source_content` | `sourceContent` | 인용 근거 텍스트 (그대로 저장) |
| `macro_snapshot` | `macroSnapshot` | 거시 텍스트 (있을 때) |
| `intent` | `intent` | 분석/통계용 (선택) |
| `verdict` / `grounded_score` | `verification.verdict` / `.groundedScore` | 신뢰도 기록 (선택) |
| `created_at` | (백엔드 시각) | |

- 구조화 출처(`sources[]`)는 보통 저장 불필요 (표시용). 필요하면 JSON 컬럼으로.
- `error != null`(추론 실패) 응답은 **이력에 저장하지 않음** (백엔드 회복정책 §7.1).

---

## 5. 운영 메모
- 컬렉션 추가 = 새 기업 적재 (`ingest_corpus.py`, corpCode별 `corpus_<corpCode>` 생성)
- 청킹 파라미터 변경 시 = 해당 코퍼스 **재적재 필요** (전체 재임베딩, 검색 일관성)
- 적재/검색은 **반드시 동일 임베딩 모델**(text-embedding-3-small) 사용
