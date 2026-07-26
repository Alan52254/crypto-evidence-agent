from __future__ import annotations

import asyncio
from typing import Any, Mapping

import pytest
from hoyabit_agent.domain import AnalysisRequest, Asset, Evidence, Facet, SourceExcerpt
from hoyabit_agent.evidence_quality import SourceTier, assess_evidence_quality
from hoyabit_agent.run import DEFAULT_IO_TIMEOUT_SECONDS, _invoke
from hoyabit_agent.seams import EvidenceSource, ToolInvocation, ToolSpec


class FailingEvidenceSource:
    def __init__(self, spec: ToolSpec, exception_to_raise: Exception) -> None:
        self._spec = spec
        self.exception = exception_to_raise

    @property
    def spec(self) -> ToolSpec:
        return self._spec

    async def fetch(self, asset: Asset, arguments: Mapping[str, Any]) -> tuple[Evidence, ...]:
        raise self.exception


@pytest.mark.asyncio
async def test_invoke_handles_source_http_error_gracefully() -> None:
    spec = ToolSpec("failing_source", "A source that throws HTTP 502", parameters={"type": "object"})
    source: EvidenceSource = FailingEvidenceSource(spec, RuntimeError("502 Bad Gateway from CoinGecko"))

    invocation = ToolInvocation("failing_source", {"asset": "BTC"})
    sources: dict[str, EvidenceSource] = {"failing_source": source}
    evidence = await _invoke(invocation, sources, Asset.BTC, timeout_seconds=5.0)

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
