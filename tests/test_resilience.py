"""Tests for system resilience, exponential backoff, and graceful degradation."""

from __future__ import annotations

import asyncio
import pytest
from hoyabit_agent.domain import AnalysisRequest, Asset, Facet, SourceExcerpt, Evidence
from hoyabit_agent.evidence_quality import assess_evidence_quality, SourceTier
from hoyabit_agent.run import _invoke, DEFAULT_IO_TIMEOUT_SECONDS
from hoyabit_agent.seams import ToolInvocation, EvidenceSource, ToolSpec


class FailingEvidenceSource:
    def __init__(self, spec: ToolSpec, exception_to_raise: Exception) -> None:
        self.spec = spec
        self.exception = exception_to_raise

    async def fetch(self, asset: Asset, arguments: dict[str, object]) -> tuple[Evidence, ...]:
        raise self.exception


@pytest.mark.asyncio
async def test_invoke_handles_source_http_error_gracefully() -> None:
    spec = ToolSpec("failing_source", "A source that throws HTTP 502", (Facet.TECHNICAL,))
    source = FailingEvidenceSource(spec, RuntimeError("502 Bad Gateway from CoinGecko"))
    
    invocation = ToolInvocation("failing_source", {"asset": "BTC"})
    evidence = await _invoke(invocation, {"failing_source": source}, Asset.BTC, timeout_seconds=5.0)

    assert evidence is None


def test_evidence_quality_includes_degradation_warning() -> None:
    # Single domain evidence should trigger limitation/warning
    excerpt = SourceExcerpt(
        source_id="src1",
        url="https://public_social.com/post/1",
        retrieved_at=pytest.importorskip("datetime").datetime.now(),
        locator="post1",
        text="Bullish post"
    )
    ev = Evidence(id="E1", facet=Facet.SENTIMENT, summary="Social evidence", stance_hint=0.8, excerpts=(excerpt,))
    
    quality = assess_evidence_quality((ev,))
    assert len(quality.limitations) > 0
    assert any("關鍵證據缺乏兩個以上獨立網域" in lim for lim in quality.limitations)
