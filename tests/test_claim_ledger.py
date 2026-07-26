"""判斷帳本 —— 撰寫與驗證分離的兌現處，無需 mock。

驗證器的職責是「不信任撰寫者」，所以測試的重點是它能否抓出
幻覺引用、薄弱支撐與矛盾，以及被拒絕的判斷有沒有變成限制說明。
"""

from __future__ import annotations

from tests.test_reliability import evidence_from

from hoyabit_agent.claim_ledger import (
    ClaimStatus,
    coverage_ratio,
    role_breakdown,
    verify,
)
from hoyabit_agent.domain import ClaimRole, DraftClaim, Facet

EXCHANGE = evidence_from(
    "BNC-BTC-RSI", "binance:spot", facet=Facet.TECHNICAL, stance_hint=0.4
)
EXCHANGE_2 = evidence_from(
    "BNC-BTC-OI", "binance:perp", facet=Facet.POSITIONING, stance_hint=0.3
)
BULLISH_NEWS = evidence_from(
    "NEWS-1", "coindesk:a", facet=Facet.FUNDAMENTAL, stance_hint=0.6
)
BEARISH_NEWS = evidence_from(
    "NEWS-2", "blockworks:b", facet=Facet.FUNDAMENTAL, stance_hint=-0.6
)
SOCIAL = evidence_from("SOC-1", "reddit:1", facet=Facet.SENTIMENT, stance_hint=0.5)

ALL = (EXCHANGE, EXCHANGE_2, BULLISH_NEWS, BEARISH_NEWS, SOCIAL)


def draft(
    text: str,
    ids: tuple[str, ...],
    *,
    facet: Facet = Facet.TECHNICAL,
    role: ClaimRole = ClaimRole.INFERENCE,
) -> DraftClaim:
    return DraftClaim(text=text, evidence_ids=ids, facet=facet, role=role)


def test_a_claim_citing_a_real_exchange_number_is_supported() -> None:
    result = verify((draft("RSI 為 46", ("BNC-BTC-RSI",), role=ClaimRole.FACT),), ALL)
    assert result.claims[0].status is ClaimStatus.SUPPORTED


def test_a_claim_citing_no_evidence_is_unsupported() -> None:
    result = verify((draft("散戶極度樂觀", ()),), ALL)
    assert result.claims[0].status is ClaimStatus.UNSUPPORTED


def test_a_hallucinated_evidence_id_is_recorded_not_silently_dropped() -> None:
    """模型憑空造出的識別碼是幻覺的直接證據，值得留存供稽核。"""
    result = verify((draft("鯨魚在吸籌", ("E-NOT-REAL",)),), ALL)
    claim = result.claims[0]
    assert claim.status is ClaimStatus.UNSUPPORTED
    assert claim.dropped_evidence_ids == ("E-NOT-REAL",)


def test_valid_citations_survive_alongside_invalid_ones() -> None:
    """部分引用無效時不整筆丟棄，但要留下剔除紀錄。"""
    result = verify((draft("均線走平", ("BNC-BTC-RSI", "E-NOT-REAL")),), ALL)
    claim = result.claims[0]
    assert claim.evidence_ids == ("BNC-BTC-RSI",)
    assert claim.dropped_evidence_ids == ("E-NOT-REAL",)
    assert claim.status is ClaimStatus.SUPPORTED


def test_a_conclusion_resting_on_a_single_source_is_contested() -> None:
    """單一證據撐不起一個結論 —— 這是結論層才有的較高門檻。"""
    result = verify(
        (draft("BTC 轉多", ("BNC-BTC-RSI",), role=ClaimRole.CONCLUSION),), ALL
    )
    claim = result.claims[0]
    assert claim.status is ClaimStatus.CONTESTED
    assert any("獨立來源" in reason for reason in claim.reasons)


def test_the_same_conclusion_with_two_independent_sources_is_supported() -> None:
    result = verify(
        (
            draft(
                "BTC 轉多",
                ("BNC-BTC-RSI", "BNC-BTC-OI"),
                role=ClaimRole.CONCLUSION,
            ),
        ),
        ALL,
    )
    assert result.claims[0].status is ClaimStatus.SUPPORTED


def test_a_claim_backed_only_by_social_posts_is_contested() -> None:
    result = verify((draft("市場很樂觀", ("SOC-1",), facet=Facet.SENTIMENT),), ALL)
    claim = result.claims[0]
    assert claim.status is ClaimStatus.CONTESTED
    assert any("低可信度" in reason for reason in claim.reasons)


def test_a_claim_citing_evidence_that_points_both_ways_is_contested() -> None:
    """引用了互相矛盾的證據卻下單一方向的判斷，必須被標記。"""
    result = verify(
        (draft("基本面偏多", ("NEWS-1", "NEWS-2"), facet=Facet.FUNDAMENTAL),), ALL
    )
    claim = result.claims[0]
    assert claim.status is ClaimStatus.CONTESTED
    assert any("方向相反" in reason for reason in claim.reasons)


def test_contested_claims_remain_admissible_to_the_report() -> None:
    """爭議不等於刪除 —— 它進報告，但必須揭露爭議。"""
    result = verify(
        (draft("BTC 轉多", ("BNC-BTC-RSI",), role=ClaimRole.CONCLUSION),), ALL
    )
    assert result.admissible == result.claims
    assert result.claims[0].admissible is True


def test_unsupported_claims_are_not_admissible() -> None:
    result = verify((draft("憑空判斷", ("E-NOT-REAL",)),), ALL)
    assert result.admissible == ()


def test_rejected_and_contested_claims_become_stated_limitations() -> None:
    """命題要求明確指出限制 —— 靜默丟棄等於丟掉這些分數。"""
    result = verify(
        (
            draft("憑空判斷", ("E-NOT-REAL",)),
            draft("BTC 轉多", ("BNC-BTC-RSI",), role=ClaimRole.CONCLUSION),
        ),
        ALL,
    )
    limitations = result.limitations()
    assert any("未能驗證的說法" in line for line in limitations)
    assert any("支撐薄弱的判斷" in line for line in limitations)


def test_coverage_ratio_counts_only_conclusions() -> None:
    """事實層本來就是引述證據，算進覆蓋率會虛高。"""
    result = verify(
        (
            draft("無效事實", ("E-NOT-REAL",), role=ClaimRole.FACT),
            draft(
                "有效結論",
                ("BNC-BTC-RSI", "BNC-BTC-OI"),
                role=ClaimRole.CONCLUSION,
            ),
        ),
        ALL,
    )
    assert coverage_ratio(result) == 1.0


def test_coverage_ratio_falls_when_a_conclusion_is_unsupported() -> None:
    result = verify(
        (
            draft("好結論", ("BNC-BTC-RSI", "BNC-BTC-OI"), role=ClaimRole.CONCLUSION),
            draft("壞結論", ("E-NOT-REAL",), role=ClaimRole.CONCLUSION),
        ),
        ALL,
    )
    assert coverage_ratio(result) == 0.5


def test_role_breakdown_reveals_whether_the_report_has_three_layers() -> None:
    """報告是否真的有事實→推論→結論，要能一眼看出來。"""
    result = verify(
        (
            draft("事實", ("BNC-BTC-RSI",), role=ClaimRole.FACT),
            draft("推論", ("BNC-BTC-RSI", "BNC-BTC-OI"), role=ClaimRole.INFERENCE),
            draft("結論", ("BNC-BTC-RSI", "BNC-BTC-OI"), role=ClaimRole.CONCLUSION),
        ),
        ALL,
    )
    breakdown = role_breakdown(result)
    assert breakdown["fact"] == 1
    assert breakdown["inference"] == 1
    assert breakdown["conclusion"] == 1
    assert breakdown["watch"] == 0
