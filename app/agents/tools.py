"""CrewAI 툴: 인덱싱된 공시에서 근거 문단을 검색한다.

analysis_id 에 바인딩된 검색 툴을 만들어 QA/Summary Agent 에 주입한다.
"""
from __future__ import annotations

import json

from crewai.tools import BaseTool
from pydantic import BaseModel, Field

from app.rag.vectorstore import get_vector_store


class _SearchArgs(BaseModel):
    query: str = Field(..., description="공시에서 찾고 싶은 내용에 대한 검색어/질문")


def make_disclosure_search_tool(analysis_id: str, top_k: int = 5) -> BaseTool:
    store = get_vector_store()

    class DisclosureSearchTool(BaseTool):
        name: str = "disclosure_search"
        description: str = (
            "현재 분석 중인 공시 원문에서 질문과 관련된 근거 문단을 검색한다. "
            "답변에 인용할 chunk_id, section_title, quote 를 얻으려면 이 툴을 사용하라."
        )
        args_schema: type[BaseModel] = _SearchArgs

        def _run(self, query: str) -> str:
            citations = store.search(analysis_id, query, top_k=top_k)
            return _citations_to_json(citations)

    return DisclosureSearchTool()


def _citations_to_json(citations) -> str:
    payload = [
        {
            "chunk_id": c.chunk_id,
            "section_title": c.section_title,
            "quote": c.quote,
            "score": c.score,
        }
        for c in citations
    ]
    return json.dumps(payload, ensure_ascii=False)


def make_corpus_search_tool(collection_name: str, top_k: int = 5) -> BaseTool:
    """기업 코퍼스(corpus_<corp_code>) 전체에서 근거를 검색하는 툴.

    핑퐁 채팅에서 방의 회사 코퍼스로 검색 범위를 한정할 때 쓴다.
    """
    store = get_vector_store()

    class CorpusSearchTool(BaseTool):
        name: str = "disclosure_search"
        description: str = (
            "이 회사의 공시 코퍼스(여러 공시)에서 질문과 관련된 근거 문단을 검색한다. "
            "답변에 인용할 chunk_id, section_title, quote 를 얻으려면 이 툴을 사용하라."
        )
        args_schema: type[BaseModel] = _SearchArgs

        def _run(self, query: str) -> str:
            citations = store.search_corpus(collection_name, query, top_k=top_k)
            return _citations_to_json(citations)

    return CorpusSearchTool()
