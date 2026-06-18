"""FastAPI 진입점.

실행: uvicorn app.main:app --reload
문서: http://localhost:8000/docs
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.storage import db


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield


app = FastAPI(
    title="공시분석 AI Agent",
    description="공시 1건을 요약·근거 기반 QA·검증하는 AI 파이프라인",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(router, prefix="/api", tags=["disclosure"])


@app.get("/health", tags=["meta"])
def health() -> dict:
    return {"status": "ok"}
