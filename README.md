# 📑 공시분석 AI Agent (FastAPI)

기업 단위 대화방에서 **공시 기반 질의응답**을 하는 핑퐁(멀티턴) 챗봇 AI.
질문을 받아 **답변 + 출처 + 거시결합 + 검증**을 단일 JSON으로 반환한다. (Spring Boot 백엔드가 동기 호출)

- **에이전트**: Router → QA → Verification (CrewAI) — *거시·재무는 코드가 수집해 QA가 통합*
- **LLM**: OpenAI `gpt-5.1`(라우터/QA) · `o4-mini`(검증)
- **RAG**: 청킹 → `text-embedding-3-small`(1536d) → Chroma(회사별 코퍼스) → 근거 검색
- **데이터**: OpenDART(공시·재무) · 한국은행 ECOS(거시) — 무인증 공개 데이터
- **무상태(stateless)**: 세션/이력은 백엔드 소유. AI는 매 요청 `messages`를 받아 답만 반환.

핵심 기능: 회사 스코프 가드 · 되묻기(clarification) · 재무결합 · 거시결합 · 검증(+CRAG 재검색) · 출처 추적.

---

## 1. 요구사항
- Python **3.11**
- OpenAI API 키 (필수), OpenDART 키 (필수), ECOS 키 (선택)

## 2. 설치
```bash
git clone <repo-url>
cd 금융
python3.11 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## 3. 환경설정 (`.env`)
`.env.example`을 복사해 키를 채운다. (`.env`는 깃에 안 올라감)
```bash
cp .env.example .env
```
```dotenv
OPENAI_API_KEY=sk-proj-...      # 필수 (LLM + 임베딩)
DART_API_KEY=...                # 필수 (재무결합·코퍼스 적재)
ECOS_API_KEY=sample             # 거시지표 (sample 가능, 실키 권장)
EMBEDDING_PROVIDER=openai
EMBEDDING_MODEL=text-embedding-3-small
```

## 4. ⚠️ 코퍼스 데이터 (꼭 읽기)
서비스가 답하려면 **Chroma 코퍼스(`data/chroma/`)가 있어야** 한다. **이 폴더는 용량(약 1.2GB)이라 깃헙에 안 올라간다.** 둘 중 하나:

**(A) 데이터 전달받기** (권장·빠름)
- 지현이 적재해둔 `data/chroma/` 폴더를 받아 프로젝트 `data/` 아래에 둔다. (삼성전자·현대자동차 3개년 적재 완료)

**(B) 직접 적재** (`DART_API_KEY` 필요)
```bash
python ingest_corpus.py          # 삼성전자·현대자동차 3개년 (수십 분, OpenAI 임베딩 비용 ~수백원)
```
> 적재·검색은 반드시 **같은 임베딩 모델**(text-embedding-3-small) 사용 (기본 설정됨).

## 5. 실행
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8001
```
| 엔드포인트 | 용도 |
|---|---|
| `POST /api/v1/chat` | 챗 추론 (백엔드 연동 대상) |
| `POST /api/v1/chat/title` | 첫 질문 → 대화 제목 요약 |
| `GET  /health` | 헬스체크 (`{"status":"ok"}`) |
| `GET  /docs` | Swagger UI |
| `GET  /openapi.json` | OpenAPI 스펙 |

## 6. 연동 테스트 (curl)
```bash
curl -s -X POST http://localhost:8001/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-Trace-Id: 11111111-1111-1111-1111-111111111111" \
  -d '{
    "roomId": 142,
    "userSeq": 12345,
    "companyContext": {"corpCode": "00126380", "corpName": "삼성전자"},
    "messages": [{"role": "user", "content": "영업이익 작년보다 얼마나 늘었어?"}]
  }'
```
기업 corpCode: 삼성전자 `00126380` · 현대자동차 `00164742`

---

## 7. 연동 문서 (`docs/`)
| 문서 | 내용 |
|---|---|
| `docs/백엔드연동_협의명세서.md` | **API·DB·인증·타임아웃·회복·합의항목 통합** (백엔드 협의용) |
| `docs/API_명세서.md` | 요청/응답 계약 상세 |
| `docs/chat_v2_schema.json` | 기계판독 JSON Schema |
| `docs/DB_명세서.md` | 저장 구조 (Chroma/SQLite) |
| `docs/실행_연동_가이드.md` | 실행·스모크 테스트 |

---

## 8. 프로젝트 구조
```
app/
  main.py                FastAPI 진입점 (라우터 prefix "/api")
  config.py              설정(.env)
  api/routes.py          엔드포인트 (POST /api/v1/chat 등)
  schemas/
    disclosure.py        내부 스키마 (ChatRequest/Response, Citation, Verification...)
    external.py          v2.0 외부 계약 스키마 (camelCase)
  services/
    chat.py              핑퐁 턴 핸들러 (라우터→검색→QA→검증→CRAG)
    contract.py          v2.0 ↔ 내부 어댑터
    macro.py             ECOS 거시 캐시
    financials.py        DART 재무결합
    pipeline.py          (레거시) 단건 분석
  agents/crew.py         Router/QA/Verification (CrewAI)
  rag/                   chunker · embedder(OpenAI) · vectorstore(Chroma)
  data/                  dart · ecos · loaders
  storage/db.py          SQLite (거시 캐시 등)
ingest_corpus.py         코퍼스 적재 스크립트
eval/                    청킹 실험 키트 (Hit@k 평가)
docs/                    연동 명세서 모음
```

---

## 9. 깃 주의사항
- `.env`(실제 키)·`data/chroma/`(1.2GB)·`data/*.db`·`.venv/`는 **`.gitignore`로 제외됨.** 절대 커밋 금지.
- 공개 저장소면 OpenAI 키는 **노출 시 즉시 폐기·재발급** 권장.

## (부록) GitHub 올리기
```bash
git add -A
git commit -m "공시분석 AI 에이전트 (FastAPI)"
git branch -M main
git remote add origin https://github.com/<계정>/<레포>.git
git push -u origin main
```
> 푸시 전 `git status`로 `.env`·`data/chroma`가 목록에 **없는지** 꼭 확인.
