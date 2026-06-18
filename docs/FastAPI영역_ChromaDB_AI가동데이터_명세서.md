# 2. FastAPI 영역 (ChromaDB + AI 가동 데이터) 명세서

> ChromaDB는 Vector DB이므로 일반 RDB식 테이블 정의가 아니라, RAG·에이전트 특성을 반영한
> **① 벡터 데이터의 구조**와 **② 컨텍스트 관리 방식**으로 기술한다.
> (실제 구현 기준 — 공시분석 AI / CrewAI + FastAPI + Chroma)

---

## 📌 ChromaDB 스키마 정의

Vector DB 스키마 = **Collection + Embedding(모델·차원) + Metadata** 세 요소.

### (1) Collection 명세
- **명명규칙**: `corpus_<corpCode>` — **기업 1개당 컬렉션 1개**
- **목적**: 해당 기업의 공시(사업·분기·반기보고서, 주요사항·발행·거래소·외부감사·선별 지분공시) 청크 집합 = **RAG 검색 대상**
- **회사 범위 격리**: 검색은 그 기업 컬렉션에서만 수행 → "다른 회사 답변 섞임" 원천 차단
- **현재 적재 현황**:

| Collection | 기업 | corpCode | 청크 수 |
|---|---|---|---|
| `corpus_00126380` | 삼성전자 | 00126380 | 60,119 |
| `corpus_00164742` | 현대자동차 | 00164742 | 77,505 |

> 기업 추가 = `ingest_corpus.py`로 새 `corpus_<corpCode>` 적재. 적재 안 된 기업은 검색 결과가 비어 "확인 불가"만 반환.

### (2) Embedding 모델 및 차원 (Dimension)
- **모델**: OpenAI **`text-embedding-3-small`**
- **차원**: **1536 dim** (이 차원이 벡터 DB 스키마 크기를 결정)
- **유사도 점수**: `score = 1 − distance` (0~1, 클수록 유사)
- **불변 규칙**: 적재와 검색은 **반드시 동일 임베딩 모델** 사용 (다르면 검색 무의미)

### (3) 메타데이터(Metadata) 스키마 (★가장 중요)
벡터와 함께 저장하는 key-value 구조. **필터링·출처 추적의 핵심.**

청크(레코드) 1건 구성:
```
id        : "<rceptNo>-c-<순번>"   예) "20260310002820-c-0910"
document  : 청크 본문 텍스트
embedding : float[1536]
metadata  : { ... 아래 ... }
```

**metadata 스키마:**
```json
{
  "corp_name":     "string",            // 기업명 (예: 삼성전자)
  "rcept_no":      "string",            // 공시 접수번호 (출처 추적 키)
  "rcept_dt":      "string(YYYYMMDD)",  // 접수일 — 날짜 필터/최신 정렬
  "report_nm":     "string",            // 공시명 (예: 사업보고서)
  "pblntf_ty":     "string",            // 공시유형 (A정기/B주요사항/C발행/D지분/F외부감사/I거래소)
  "section_title": "string",            // 인용된 섹션 제목
  "order":         "integer"            // 문서 내 청크 순번
}
```

**필터링(하이브리드) 활용:**
| 조건 | 방식 |
|---|---|
| 회사 한정 | Collection 분리 (`corpus_<corpCode>`) |
| 기간 ("작년", "2024년") | `rcept_dt` 범위 필터 |
| "최근/가장 최근" | `rcept_dt` 최신 정렬 |
| 출처 카드/인용 | `rcept_no`·`report_nm`·`rcept_dt`·`section_title`로 응답 `sources[]` 생성 |

> 즉 **"무엇"은 임베딩 벡터(의미 검색)**, **"어느 회사·언제"는 metadata**로 분리 처리.

---

## 🤖 에이전트(Agent) 및 RAG 관련 추가 저장 방식

단순 조회용 벡터 외에, 에이전트 구동에 필요한 "저장" 데이터의 위치·구조.

### (4) 프롬프트 히스토리 / 세션 메모리 (Short-term Memory)

| 항목 | 우리 설계 |
|---|---|
| **어디에 저장?** | **AI는 저장하지 않음 (Stateless).** Chat History 원본은 **백엔드(PostgreSQL)** 가 보관 |
| FastAPI 서버 메모리 | ❌ 사용 안 함 |
| Redis | ❌ 사용 안 함 |
| **전달 구조** | 백엔드가 **매 요청마다 `messages[]`** (role/content 배열, 시간순)로 동봉 |
| **AI 사용 범위** | 받은 `messages` 중 **최근 10개 메시지만** 프롬프트에 사용 (토큰 안전장치) |
| **지시어·되묻기 해소** | 이 단기 메모리로 "그거/방금" 같은 표현 및 되묻기 후속 턴 해석 |

> **설계 의도**: AI를 무상태로 두어 **수평 확장 안전**(어느 인스턴스가 받아도 동일 동작) + 세션 동기화 이슈 제거. 세션·만료(30분)·이력 영속화는 전부 백엔드 책임.

```
[대화 흐름]
브라우저 → 백엔드(세션·이력 DB) → messages[] 동봉 → FastAPI(무상태) → 답변
                ↑ Chat History 원본 저장은 여기(PostgreSQL)
```

### (5) 에이전트 상태(State) 관리

| 항목 | 우리 설계 |
|---|---|
| **프레임워크** | **CrewAI** (LangChain / LangGraph 아님) |
| **State 영속화** | **하지 않음.** 한 요청 = 단발(single-pass) 파이프라인 실행 후 종료 |
| **파이프라인** | Router(의도·스코프·검색어·날짜) → 코퍼스 검색(+재무 DART, +거시 ECOS) → QA(통합 답변) → Verification(검증) → *fail 시 CRAG 재검색 1회* |
| **Step/Checkpoint** | LangGraph식 step checkpoint 없음. 재시도·되묻기는 **한 요청 내부** 또는 다음 요청의 `messages`로 처리 |
| **유일한 AI 영속 데이터** | 대화와 무관한 **거시 캐시(SQLite `macro_cache`)** + 사전 적재 코퍼스(Chroma)뿐 |

**SQLite (`./data/app.db`) — AI측 영속 데이터:**
| 테이블 | 컬럼 | 용도 |
|---|---|---|
| `macro_cache` | `as_of`(PK, YYYYMMDD), `payload`(JSON) | ECOS 거시 스냅샷 날짜별 캐시 (과거값 불변 → 1회 호출 후 재사용) |
| `analyses`·`chat_turns` | (레거시) | 초기 단건분석 경로 — 현재 챗 플로우 미사용 |

---

## 요약 (한눈에)

| 구분 | 저장소 | 내용 | 소유 |
|---|---|---|---|
| 벡터(공시 코퍼스) | **Chroma** `corpus_<corpCode>` | 청크+1536d 임베딩+metadata 7필드 | AI |
| 거시 캐시 | **SQLite** `macro_cache` | 날짜별 거시 스냅샷 | AI |
| **단기 메모리(Chat History)** | **백엔드 PostgreSQL** | 대화 이력 (AI엔 messages로 전달) | **백엔드** |
| 에이전트 State | (없음) | 무상태 단발 실행, 영속화 X | — |

> 핵심: **AI는 "검색용 벡터(Chroma)"와 "거시 캐시"만 가지며, 대화 상태는 무상태.** 컨텍스트(이력)는 백엔드가 매 요청 주입, 에이전트 State는 영속화하지 않는다.
