"""SQLite 영속 저장.

분석 결과 전체를 JSON 으로 보관하고, 목록 조회용 컬럼을 별도로 둔다.
요약/응답 결과를 사후 조회(목록/상세)할 수 있게 한다.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from app.config import get_settings
from app.schemas.disclosure import (
    AnalysisListItem,
    AnalysisResult,
    ChatTurn,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS analyses (
    analysis_id  TEXT PRIMARY KEY,
    company_name TEXT,
    title        TEXT,
    headline     TEXT,
    status       TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_analyses_created ON analyses(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_analyses_company ON analyses(company_name, created_at DESC);

-- 단기 메모리: 공시(세션)별 대화 내역
CREATE TABLE IF NOT EXISTS chat_turns (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    analysis_id  TEXT NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat_turns_analysis ON chat_turns(analysis_id, id);

-- 거시지표 스냅샷 캐시 (날짜별, 과거값 불변 → lazy-fill)
CREATE TABLE IF NOT EXISTS macro_cache (
    as_of    TEXT PRIMARY KEY,
    payload  TEXT NOT NULL
);
"""


def _connect() -> sqlite3.Connection:
    path = Path(get_settings().sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with _connect() as conn:
        conn.executescript(_SCHEMA)
        # 기존 DB 호환: headline 컬럼이 없으면 추가
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(analyses)")}
        if "headline" not in cols:
            conn.execute("ALTER TABLE analyses ADD COLUMN headline TEXT")
        conn.commit()


def save_analysis(result: AnalysisResult) -> None:
    headline = result.summary.headline if result.summary else None
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO analyses (analysis_id, company_name, title, headline, status, created_at, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(analysis_id) DO UPDATE SET
                company_name=excluded.company_name,
                title=excluded.title,
                headline=excluded.headline,
                status=excluded.status,
                payload=excluded.payload
            """,
            (
                result.analysis_id,
                result.company_name,
                result.title,
                headline,
                result.status.value,
                result.created_at.isoformat(),
                result.model_dump_json(),
            ),
        )
        conn.commit()


def get_analysis(analysis_id: str) -> AnalysisResult | None:
    with _connect() as conn:
        row = conn.execute(
            "SELECT payload FROM analyses WHERE analysis_id = ?", (analysis_id,)
        ).fetchone()
    if not row:
        return None
    return AnalysisResult.model_validate(json.loads(row["payload"]))


def list_analyses(
    limit: int = 50, offset: int = 0, company: str | None = None
) -> tuple[list[AnalysisListItem], int]:
    """마이페이지 목록. company 가 주어지면 해당 기업으로 필터(탭)."""
    where = "WHERE company_name = ?" if company else ""
    params: tuple = (company,) if company else ()
    with _connect() as conn:
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM analyses {where}", params
        ).fetchone()["n"]
        rows = conn.execute(
            f"""
            SELECT analysis_id, company_name, title, headline, status, created_at
            FROM analyses {where} ORDER BY created_at DESC LIMIT ? OFFSET ?
            """,
            (*params, limit, offset),
        ).fetchall()
    items = [
        AnalysisListItem(
            analysis_id=r["analysis_id"],
            company_name=r["company_name"],
            title=r["title"],
            headline=r["headline"],
            status=r["status"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    return items, total


def list_companies() -> list[str]:
    """마이페이지 탭 구성용 — 분석된 기업 목록(중복 제거)."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT company_name FROM analyses "
            "WHERE company_name IS NOT NULL AND company_name <> '' ORDER BY company_name"
        ).fetchall()
    return [r["company_name"] for r in rows]


# ============================================================
# 단기 메모리: 대화 내역
# ============================================================
def add_chat_turns(analysis_id: str, turns: list[ChatTurn]) -> None:
    from datetime import datetime

    now = datetime.utcnow().isoformat()
    with _connect() as conn:
        conn.executemany(
            "INSERT INTO chat_turns (analysis_id, role, content, created_at) VALUES (?, ?, ?, ?)",
            [(analysis_id, t.role, t.content, now) for t in turns],
        )
        conn.commit()


def get_chat_history(analysis_id: str, limit: int = 20) -> list[ChatTurn]:
    """해당 공시의 최근 대화 내역을 시간순으로 반환."""
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT role, content FROM chat_turns
            WHERE analysis_id = ? ORDER BY id DESC LIMIT ?
            """,
            (analysis_id, limit),
        ).fetchall()
    rows = list(reversed(rows))  # 오래된 → 최신 순
    return [ChatTurn(role=r["role"], content=r["content"]) for r in rows]


# ============================================================
# 거시지표 캐시 (날짜별 lazy-fill)
# ============================================================
_MACRO_DDL = (
    "CREATE TABLE IF NOT EXISTS macro_cache (as_of TEXT PRIMARY KEY, payload TEXT NOT NULL)"
)


def get_macro_cache(as_of: str) -> dict | None:
    with _connect() as conn:
        conn.execute(_MACRO_DDL)  # init_db 미실행 환경에서도 안전
        row = conn.execute(
            "SELECT payload FROM macro_cache WHERE as_of = ?", (as_of,)
        ).fetchone()
    return json.loads(row["payload"]) if row else None


def set_macro_cache(as_of: str, snapshot: dict) -> None:
    with _connect() as conn:
        conn.execute(_MACRO_DDL)
        conn.execute(
            "INSERT OR REPLACE INTO macro_cache (as_of, payload) VALUES (?, ?)",
            (as_of, json.dumps(snapshot, ensure_ascii=False)),
        )
        conn.commit()
