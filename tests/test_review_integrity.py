"""針對性測試 — 驗證三層審查架構對已知問題案例的行為。"""

from __future__ import annotations

import pytest

from hoyabit_agent.domain import (
    ClaimRole,
    Confidence,
    DraftClaim,
    Evidence,
    Facet,
    SourceExcerpt,
    Stance,
)
from hoyabit_agent.review import _validate_revision, enforce_paired_disclosure
from hoyabit_agent.tools import assess_confidence
from datetime import datetime, UTC


def _make_evidence(eid: str, facet: Facet, hint: float) -> Evidence:
    return Evidence(
        id=eid,
        facet=facet,
        summary=f"summary for {eid}",
        stance_hint=hint,
        excerpts=(
            SourceExcerpt(
                source_id=eid,
                url="",
                retrieved_at=datetime.now(UTC),
                locator="",
                text=f"text for {eid}",
            ),
        ),
    )


class TestConfidenceContradictionPenalty:
    """Layer 2.1: 面向矛盾必須扣分。"""

    def test_positioning_bullish_vs_bearish_majority_reduces_agreement(self) -> None:
        """positioning=bullish + tech=bearish + sentiment=bearish → agreement 被扣分。"""
        evidence = (
            _make_evidence("BNC-SPOT-BTC-1d-SMA200", Facet.TECHNICAL, -0.6),
            _make_evidence("BNC-PERP-BTC-FUNDING", Facet.POSITIONING, +0.5),
            _make_evidence("FGI-CURRENT", Facet.SENTIMENT, -0.4),
            _make_evidence("CG-BTC-MARKET", Facet.FUNDAMENTAL, 0.0),
        )
        result = assess_confidence(evidence)
        assert isinstance(result, Confidence)
        # agreement 應該被扣分（positioning 跟多數方向矛盾）
        assert result.agreement < 0.5, f"Expected agreement < 0.5, got {result.agreement}"
        # 總信心度不應該是 85%+
        assert result.value < 0.75, f"Expected confidence < 75%, got {result.value:.0%}"

    def test_all_facets_agree_gives_high_agreement(self) -> None:
        """所有面向一致 → agreement 高。"""
        evidence = (
            _make_evidence("BNC-SPOT-BTC-1d-SMA200", Facet.TECHNICAL, +0.6),
            _make_evidence("BNC-PERP-BTC-FUNDING", Facet.POSITIONING, +0.5),
            _make_evidence("FGI-CURRENT", Facet.SENTIMENT, +0.4),
            _make_evidence("FRED-M2SL", Facet.FUNDAMENTAL, +0.3),
        )
        result = assess_confidence(evidence)
        assert isinstance(result, Confidence)
        assert result.agreement > 0.5


class TestPairedDisclosure:
    """Layer 1.1: 配對指標必須強制揭露（規則式，不依賴 LLM）。"""

    def test_sma200_cited_without_sma60_gets_supplemented(self) -> None:
        """引用 SMA200 但漏了 SMA60 → 程式碼強制附上 SMA60。"""
        evidence = (
            _make_evidence("BNC-SPOT-BTC-1d-SMA60", Facet.TECHNICAL, +0.1),
            _make_evidence("BNC-SPOT-BTC-1d-SMA200", Facet.TECHNICAL, -0.5),
        )
        claims = (
            DraftClaim(
                text="BTC 低於 200 日均線，技術面偏空。",
                evidence_ids=("BNC-SPOT-BTC-1d-SMA200",),
                facet=Facet.TECHNICAL,
                role=ClaimRole.CONCLUSION,
            ),
        )
        result = enforce_paired_disclosure(claims, evidence)
        # SMA60 的數值應該被強制附上
        assert "SMA60" in result[0].text, f"Expected SMA60 in text, got: {result[0].text}"

    def test_both_cited_no_change(self) -> None:
        """兩者都引用了 → 不觸發補充。"""
        evidence = (
            _make_evidence("BNC-SPOT-BTC-1d-SMA60", Facet.TECHNICAL, +0.1),
            _make_evidence("BNC-SPOT-BTC-1d-SMA200", Facet.TECHNICAL, -0.5),
        )
        claims = (
            DraftClaim(
                text="BTC 站上 60 日均線但低於 200 日均線。",
                evidence_ids=("BNC-SPOT-BTC-1d-SMA60", "BNC-SPOT-BTC-1d-SMA200"),
                facet=Facet.TECHNICAL,
                role=ClaimRole.CONCLUSION,
            ),
        )
        result = enforce_paired_disclosure(claims, evidence)
        # 沒有被改（因為兩者都已引用）
        assert result[0].text == claims[0].text

    def test_neither_cited_no_change(self) -> None:
        """都沒引用 → 不觸發（claim 跟均線無關）。"""
        evidence = (
            _make_evidence("BNC-SPOT-BTC-1d-SMA60", Facet.TECHNICAL, +0.1),
            _make_evidence("BNC-SPOT-BTC-1d-SMA200", Facet.TECHNICAL, -0.5),
        )
        claims = (
            DraftClaim(
                text="M2 貨幣供給增加。",
                evidence_ids=("FRED-M2SL",),
                facet=Facet.FUNDAMENTAL,
                role=ClaimRole.FACT,
            ),
        )
        result = enforce_paired_disclosure(claims, evidence)
        assert result[0].text == claims[0].text


class TestReviewValidation:
    """Layer 3.2: 輸出約束驗證零容忍新數字。"""

    def test_rejects_new_numbers(self) -> None:
        """revised_text 有新數字 → 拒絕。"""
        original = DraftClaim(
            text="BTC 低於均線 71902 美元。",
            evidence_ids=("BNC-SPOT-BTC-1d-SMA200",),
            facet=Facet.TECHNICAL,
            role=ClaimRole.CONCLUSION,
        )
        # 引入了 65000 這個原本不存在的數字
        assert _validate_revision(original, "BTC 低於均線 71902 美元，支撐位在 65000。") is False

    def test_accepts_same_numbers_different_wording(self) -> None:
        """只改措辭不改數字 → 接受。"""
        original = DraftClaim(
            text="BTC 確認低於均線 71902 美元。",
            evidence_ids=("BNC-SPOT-BTC-1d-SMA200",),
            facet=Facet.TECHNICAL,
            role=ClaimRole.CONCLUSION,
        )
        assert _validate_revision(original, "BTC 可能受制於均線 71902 美元。") is True

    def test_rejects_excessive_length_change(self) -> None:
        """長度改太多 → 拒絕。"""
        original = DraftClaim(
            text="短線偏空。",
            evidence_ids=("BNC-SPOT-BTC-1d-SMA200",),
            facet=Facet.TECHNICAL,
            role=ClaimRole.CONCLUSION,
        )
        long_text = "這是一段非常非常長的改寫" * 10
        assert _validate_revision(original, long_text) is False
