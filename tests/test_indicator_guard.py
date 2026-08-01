"""確定性指標引用檢核 — 幻覺數字掃描的行為測試。"""

from __future__ import annotations

from hoyabit_agent.domain import DraftClaim, Evidence, Facet, SourceExcerpt
from hoyabit_agent.indicator_guard import enforce_indicator_citations


def _evidence(id: str, text: str) -> Evidence:
    return Evidence(
        id=id,
        facet=Facet.TECHNICAL,
        summary=text,
        stance_hint=0.0,
        excerpts=(
            SourceExcerpt(
                source_id="test-source",
                url="https://example.com",
                retrieved_at=__import__("datetime").datetime(2026, 8, 1, tzinfo=__import__("datetime").UTC),
                locator="test",
                text=text,
            ),
        ),
    )


def test_rsi_value_backed_by_evidence_survives() -> None:
    evidence = (_evidence("RSI14", "BNBUSDT 14 期 RSI 為 56.0"),)
    claim = DraftClaim(
        text="日線 RSI(14) = 56.0，位於中性偏多區間。",
        evidence_ids=("RSI14",),
        facet=Facet.TECHNICAL,
    )

    result = enforce_indicator_citations((claim,), evidence)

    assert result[0].text == claim.text


def test_orphan_rsi_value_is_removed() -> None:
    evidence = (_evidence("RSI14", "BNBUSDT 14 期 RSI 為 40.2"),)
    claim = DraftClaim(
        text="日線 RSI(14) = 99.9，處於極端超買。",
        evidence_ids=("RSI14",),
        facet=Facet.TECHNICAL,
    )

    result = enforce_indicator_citations((claim,), evidence)

    assert "99.9" not in result[0].text


def test_self_computed_annualized_volatility_without_evidence_is_removed() -> None:
    """指標守衛應涵蓋自算的年化波動率／標準差類敘述，不只 RSI/MACD/KD 這類緊鄰數字。

    這個案例來自實際報告：模型從 5 天資料集小表自行推算「30 日年化波動率」，
    完全沒有 evidence_id 支撐，屬於 prompts.py 明文禁止的「自行推算…標準差等
    需要精確遞迴公式的指標」，但舊版 _INDICATOR_NAMES 沒有涵蓋「波動率」關鍵字，
    這類數字會直接繞過掃描。
    """
    evidence = (_evidence("MARKET-BNB-2026-05-30", "2026-05-30 收盤 718.74，日報酬 +11.76%"),)
    claim = DraftClaim(
        text="30 日年化波動率約 47%，對應日均波動約 2.96%。",
        evidence_ids=("MARKET-BNB-2026-05-30",),
        facet=Facet.TECHNICAL,
    )

    result = enforce_indicator_citations((claim,), evidence)

    assert "47" not in result[0].text
