"""Local cross-encoder reranker (US-174; FEAT-062): a Hugging Face text-embeddings-inference ``/rerank`` endpoint
serving ``BAAI/bge-reranker-v2-m3`` inside the platform, so retrieved text never leaves it."""

from __future__ import annotations

from typing import Any

import httpx

from tela_common.errors import ErrorCode, TelaError


class TeiReranker:
    name = "tei"

    def __init__(self, base_url: str, *, timeout: float = 10.0, http: httpx.AsyncClient | None = None) -> None:
        self._http = http or httpx.AsyncClient(base_url=base_url, timeout=timeout)

    async def rerank(self, *, query: str, texts: list[str]) -> list[float]:
        """One relevance score per text, in the texts' order (sigmoid, 0 to 1)."""
        try:
            resp = await self._http.post("/rerank", json={"query": query, "texts": texts, "raw_scores": False,
                                                          "truncate": True})
        except httpx.TimeoutException as exc:
            raise TelaError(ErrorCode.LLM_TIMEOUT, "The reranker did not answer in time.") from exc
        except httpx.HTTPError as exc:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The reranker is unavailable.") from exc
        if resp.status_code >= 400:
            raise TelaError(ErrorCode.PLANNER_UNAVAILABLE, "The reranker is unavailable.",
                            {"status": resp.status_code})
        rows: list[dict[str, Any]] = resp.json()
        scores = [0.0] * len(texts)
        seen = set()
        for r in rows:
            i = int(r["index"])
            if 0 <= i < len(texts):
                scores[i] = float(r["score"])
                seen.add(i)
        if len(seen) != len(texts):
            raise TelaError(ErrorCode.INVALID_OUTPUT, "The reranker did not score every text.")
        return scores
