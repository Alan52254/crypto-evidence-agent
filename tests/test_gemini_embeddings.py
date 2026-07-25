from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from hoyabit_agent.ingest.embeddings import GeminiEmbedder


async def capture_request(
    *, task_type: str = "RETRIEVAL_QUERY"
) -> tuple[GeminiEmbedder, dict[str, Any]]:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={"embeddings": [{"values": [0.0] * 768}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = GeminiEmbedder(client, "secret", task_type=task_type)  # type: ignore[arg-type]
        await embedder.embed(["BTC window"])
    return embedder, captured


@pytest.mark.asyncio
async def test_gemini_embedder_defaults_to_768_dimensions() -> None:
    embedder, _ = await capture_request()
    assert embedder.dimensions == 768


@pytest.mark.asyncio
async def test_document_embedder_sends_retrieval_document_task() -> None:
    _, captured = await capture_request(task_type="RETRIEVAL_DOCUMENT")
    assert captured["requests"][0]["taskType"] == "RETRIEVAL_DOCUMENT"


@pytest.mark.asyncio
async def test_embedding_request_uses_the_configured_model() -> None:
    _, captured = await capture_request()
    assert captured["model"] == "models/gemini-embedding-001"


@pytest.mark.asyncio
async def test_embedding_request_asks_for_768_dimensions() -> None:
    _, captured = await capture_request()
    assert captured["requests"][0]["outputDimensionality"] == 768


@pytest.mark.asyncio
async def test_gemini_embedder_rejects_wrong_response_dimensions() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [{"values": [1.0]}]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        embedder = GeminiEmbedder(client, "secret")
        with pytest.raises(RuntimeError, match="768"):
            await embedder.embed(["query"])
