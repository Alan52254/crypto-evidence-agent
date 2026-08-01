"""快取裝飾器 — 為任意 EvidenceSource 加上 DynamoDB 查詢快取。

設計決策：
- 透明包裝，不改變底層 source 的行為
- Cache Hit 時在 Evidence 的 source_id 標註來源為 DynamoDB
- Cache Miss 時正常查詢並寫入快取
- 快取失敗不阻斷查詢（fallback 由 DynamoDBCache 處理）

此模組**有外部 I/O**（DynamoDB 讀寫），符合接縫 1 的邊界定義。
"""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any

from hoyabit_agent.domain import AnalysisRegime, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.seams import Arguments, EvidenceSource, ToolSpec
from hoyabit_agent.storage.cache_dynamodb import DynamoDBCache, get_cache

logger = logging.getLogger(__name__)

DEFAULT_TTL_SECONDS = 300  # 5 分鐘


class CachedEvidenceSource:
    """為底層 EvidenceSource 加上 DynamoDB 查詢快取的裝飾器。

    滿足 EvidenceSource 協定：
    * spec / supported_regimes 直接代理底層
    * fetch 先查快取，miss 才呼叫底層
    * 快取結果帶有 "aws_dynamodb_cache://hoyabit_query_cache" 標註
    """

    def __init__(
        self,
        inner: EvidenceSource,
        cache: DynamoDBCache | None = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._inner = inner
        self._cache = cache or get_cache()
        self._ttl_seconds = ttl_seconds

    @property
    def spec(self) -> ToolSpec:
        return self._inner.spec

    @property
    def supported_regimes(self) -> frozenset[AnalysisRegime]:
        return self._inner.supported_regimes

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        """先查 DynamoDB 快取；miss 才呼叫底層 source。"""
        cache_key = DynamoDBCache.build_cache_key(
            self._inner.spec.name, asset.value, dict(arguments)
        )

        # ─── Cache Lookup ───
        cached = self._cache.get_cached_query(cache_key)
        if cached is not None:
            # 防禦：若快取內容為空（上次查詢失敗被誤存），視為 miss
            evidence_items = cached.get("evidence", [])
            if not evidence_items:
                logger.info(
                    "[CachedSource] Cache HIT but EMPTY content, treating as MISS: %s",
                    cache_key[:60],
                )
            else:
                logger.info(
                    "[CachedSource] Cache HIT: %s (tool=%s, asset=%s)",
                    cache_key[:60],
                    self._inner.spec.name,
                    asset.value,
                )
                return self._deserialize_evidence(cached, asset)

        # ─── Cache Miss → 呼叫底層 ───
        logger.info(
            "[CachedSource] Cache MISS: %s (tool=%s, asset=%s)",
            cache_key[:60],
            self._inner.spec.name,
            asset.value,
        )
        results = await self._inner.fetch(asset, arguments)

        # 只快取非空結果 — 空結果不寫入，避免暫時性失敗阻塞後續查詢
        if results:
            serialized = self._serialize_evidence(results)
            self._cache.set_cached_query(cache_key, serialized, self._ttl_seconds)

        return results

    # ─── Serialization ───

    def _serialize_evidence(self, evidence_tuple: tuple[Evidence, ...]) -> dict[str, Any]:
        """將 Evidence tuple 序列化為可 JSON 儲存的結構。"""
        items = []
        for ev in evidence_tuple:
            excerpts = []
            for ex in ev.excerpts:
                excerpts.append({
                    "source_id": ex.source_id,
                    "url": ex.url,
                    "retrieved_at": ex.retrieved_at.isoformat(),
                    "locator": ex.locator,
                    "text": ex.text,
                })
            items.append({
                "id": ev.id,
                "facet": ev.facet.value,
                "summary": ev.summary,
                "stance_hint": ev.stance_hint,
                "excerpts": excerpts,
                "event_key": ev.event_key,
            })
        return {"evidence": items, "cached_at": time.time()}

    def _deserialize_evidence(
        self, data: dict[str, Any], asset: Asset
    ) -> tuple[Evidence, ...]:
        """從快取資料還原 Evidence tuple，標註來源為 DynamoDB cache。"""
        items = data.get("evidence", [])
        results: list[Evidence] = []

        for item in items:
            excerpts: list[SourceExcerpt] = []
            for ex_data in item.get("excerpts", []):
                excerpts.append(SourceExcerpt(
                    source_id="aws_dynamodb_cache://hoyabit_query_cache",
                    url=ex_data.get("url", ""),
                    retrieved_at=datetime.fromisoformat(ex_data["retrieved_at"]),
                    locator=ex_data.get("locator", ""),
                    text=ex_data.get("text", ""),
                ))

            try:
                facet = Facet(item["facet"])
            except (ValueError, KeyError):
                facet = Facet.TECHNICAL

            results.append(Evidence(
                id=item.get("id", f"CACHE-{asset.value}"),
                facet=facet,
                summary=item.get("summary", ""),
                stance_hint=float(item.get("stance_hint", 0.0)),
                excerpts=tuple(excerpts),
                event_key=item.get("event_key", ""),
            ))

        return tuple(results)


def wrap_with_cache(
    source: EvidenceSource,
    cache: DynamoDBCache | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> CachedEvidenceSource:
    """工廠函式：將任意 EvidenceSource 包裝為帶快取版本。"""
    return CachedEvidenceSource(source, cache=cache, ttl_seconds=ttl_seconds)


__all__ = ["CachedEvidenceSource", "wrap_with_cache"]
