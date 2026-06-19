# 공시분석 AI Agent (FastAPI)

OpenDART 공시를 **요약 · 근거(출처) 기반 QA · 답변 검증**하는 AI 파이프라인.
백엔드(Spring)에서 `POST /api/v1/chat` 하나로 호출하면, 회사 단위 멀티턴 대화에 대해
공시 근거 + (필요 시)재무·거시지표를 결합한 답변과 출처·검증결과를 동기 JSON으로 돌려줍니다.

> CrewAI + FastAPI · OpenAI(gpt-5.1 / o4-mini / text-embedding-3-small) · ChromaDB(RAG)

---

## ⚡ TL;DR (5단계)

```bash
# 1. 클론 & 가상환경
git clone https://github.com/jx-hxxx/gongsi-agent.git
cd gongsi-agent
python3.11 -m venv .venv && source .venv/bin/activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정 (.env 만들기) — API 키는 지현에게 받기 (아래 4번 참고)
cp .env.example .env
#   → .env 열어서 OPENAI_API_KEY / DART_API_KEY / ECOS_API_KEY 채우기

# 4. ⚠️ 벡터DB 적재 (필수! 안 하면 검색 결과가 비어서 답을 못 함)
python ingest_corpus.py --dry-run     # 먼저 목록만 확인(빠름)
python ingest_corpus.py               # 실제 임베딩 적재 (10~30분, OpenAI 비용 발생)

# 5. 서버 실행
uvicorn app.main:app --port 8000
#   → http://localhost:8000/docs 에서 API 확인
```

---

## 🚨 가장 중요 — 벡터DB는 비어있는 상태로 받습니다

이 레포에는 **임베딩된 벡터DB(`data/chroma/`)가 들어있지 않습니다** (용량 1.2GB라 깃에서 제외).
**클론 직후엔 검색할 데이터가 0개**라서, 채팅을 호출하면 "근거 없음"만 나옵니다.

➡️ **반드시 4번 `python ingest_corpus.py` 를 먼저 실행해서 공시를 적재**해야 정상 동작합니다.
(적재 = DART에서 공시 원문 받아 → 청킹 → OpenAI 임베딩 → Chroma 저장. 한 번만 하면 됩니다.)

적재 대상(기본): **삼성전자 · 현대자동차**, 기간 `2023-06-16 ~ 2026-06-16`, 정기/주요사항/발행/외부감사/거래소 + 대량보유·최대주주 공시.

---

## 📋 사전 요구사항

| 항목 | 값 |
|---|---|
| Python | **3.11 권장** (3.10~3.12 OK) |
| OS | macOS / Linux (Windows는 WSL 권장) |
| API 키 | OpenAI, OpenDART, (선택)ECOS — 아래 참고 |

---

## 🔑 환경변수 (.env)

`cp .env.example .env` 후 아래 3개를 채웁니다. **API 키는 깃헙에 안 올라가 있으니 지현에게 직접 받으세요** (Slack DM).

```env
OPENAI_API_KEY=sk-proj-...     # ← 지현에게 받기 (gpt-5.1 / 임베딩 호출용)
DART_API_KEY=...               # ← 지현에게 받기 (https://opendart.fss.or.kr 무료 발급도 가능)
ECOS_API_KEY=sample            # ← 거시지표(환율/금리/KOSPI)용. 없으면 sample로 둬도 채팅은 동작
```

나머지(모델명·청킹·검증 임계값·저장경로)는 `.env.example` 기본값 그대로 두면 됩니다.
> 청킹은 **800자/오버랩 120자**로 확정 (controlled 비교로 검증), 검색은 keyword fallback 적용됨 — 건드릴 필요 없음.

---

## ▶️ 실행

```bash
uvicorn app.main:app --port 8000      # 포트가 백엔드와 겹치면 --port 8001 등으로 변경
```

- 헬스체크: `curl http://localhost:8000/health` → `{"status":"ok"}`
- API 문서(Swagger): http://localhost:8000/docs

---

## 🔌 백엔드 연동 — 핵심 API: `POST /api/v1/chat`

회사 단위 멀티턴 채팅. **동기 JSON, camelCase.** 마지막 user 메시지가 이번 질문, 나머지는 history.

### 요청
```json
POST /api/v1/chat
Content-Type: application/json
X-Trace-Id: (선택, 로그 추적용 — 주면 응답 헤더로 그대로 돌려줌)

{
  "roomId": 12,
  "userSeq": 3,
  "companyContext": { "corpCode": "00126380", "corpName": "삼성전자" },
  "messages": [
    { "role": "user", "content": "삼성전자 보통주 발행주식총수 알려줘" }
  ]
}
```

### 응답
```json
{
  "roomId": 12,
  "intent": "qa",
  "answerText": "삼성전자의 보통주 발행주식총수는 5,969,782,550주입니다. ...",
  "sourceContent": "[20250311001085 / 삼성전자 사업보고서]\n보통주 발행주식총수 5,969,782,550 ...",
  "macroSnapshot": null,
  "sources": [
    {
      "rceptNo": "20250311001085",
      "reportNm": "사업보고서",
      "rceptDt": "20250311",
      "sectionTitle": "주식의 총수",
      "quote": "보통주 발행주식총수 5,969,782,550 ...",
      "score": 0.82,
      "dartUrl": "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20250311001085"
    }
  ],
  "outOfScope": false,
  "detectedCompany": null,
  "needsClarification": false,
  "verification": { "verdict": "pass", "groundedScore": 0.9 },
  "error": null
}
```

### 필드 의미 (응답)
| 필드 | 설명 |
|---|---|
| `answerText` | 최종 답변. 실패 시 `null`(이때 `error` 채워짐) |
| `sourceContent` | 출처 텍스트(사람이 읽는 포맷). 화면에 그대로 노출 가능 |
| `sources[]` | 출처 목록. `dartUrl` = DART 원문 바로가기 링크 |
| `macroSnapshot` | 환율/금리/KOSPI가 답변에 쓰였을 때만 채워짐 (아니면 null) |
| `outOfScope` | 다른 회사를 물으면 true + `detectedCompany`에 감지된 회사명 |
| `needsClarification` | 질문이 모호해 되물어야 할 때 true |
| `verification` | 답변 근거 검증. `verdict` = pass / partial / fail, `groundedScore` 0~1 |
| `error` | `{code, message, retriable}` — 완전 실패 시에만 |

> 멀티턴: 이전 대화는 `messages`에 순서대로 쌓아서 보내면 됩니다(최근 10개만 사용).
> AI 서버는 **stateless** — 세션/유저 식별은 `roomId`/`userSeq`로 백엔드가 관리합니다.

### curl 빠른 테스트
```bash
curl -s http://localhost:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "roomId": 1, "userSeq": 1,
    "companyContext": {"corpCode":"00126380","corpName":"삼성전자"},
    "messages": [{"role":"user","content":"보통주 발행주식총수 알려줘"}]
  }' | python -m json.tool
```

---

## 🧯 자주 막히는 곳

| 증상 | 원인 / 해결 |
|---|---|
| 답변이 늘 "근거 없음" / sources 빈 배열 | **벡터DB 미적재.** `python ingest_corpus.py` 실행했는지 확인 |
| `OPENAI_API_KEY` 관련 401 | `.env`의 키 확인. 가상환경 활성화(`source .venv/bin/activate`) 했는지 |
| DART 호출 실패 / 빈 목록 | `DART_API_KEY` 확인 (일 1만건 한도) |
| `ModuleNotFoundError` | `pip install -r requirements.txt` 다시. Python 3.11인지 확인 |
| 포트 충돌 | `--port 8001` 등으로 변경 |
| 적재가 너무 오래 걸림 | 정상(정기공시 임베딩이 큼). `--dry-run`으로 먼저 규모 확인 가능 |

---

## 📁 구조 요약

```
app/
  main.py            # FastAPI 진입점 (uvicorn app.main:app)
  api/routes.py      # 엔드포인트 (핵심: POST /api/v1/chat)
  services/          # chat(턴 처리) · contract(v2 어댑터) · macro · financials
  agents/crew.py     # 라우터/QA/요약/검증 (retrieve-then-read)
  rag/               # chunker · embedder · vectorstore · rerank(+keyword fallback)
  data/              # dart(OpenDART) · ecos(한국은행) 연동
  storage/db.py      # SQLite (거시 캐시 등)
ingest_corpus.py     # ⚠️ 벡터DB 적재 스크립트 (실행 필수)
docs/                # 백엔드 연동 상세 명세서 (스키마·DB·연동 가이드)
eval/                # 검색 성능 평가 (골든셋)
```

상세 연동 스펙은 **`docs/` 폴더**에 있습니다 (API 명세서 · DB 명세서 · 연동 협의 명세서 등).

---

## ⚙️ 기타

- **모델**: 라우터/QA/요약/거시 = `gpt-5.1`, 검증 = `o4-mini`, 임베딩 = `text-embedding-3-small`
- **검색**: retrieve-then-read RAG + 도메인 rerank + 정량 표 keyword fallback (정량 검색 Hit 0.41→0.65)
- **비교 질의 미지원**: 한 번에 한 회사만. 다른 회사 물으면 `outOfScope=true`로 안내
