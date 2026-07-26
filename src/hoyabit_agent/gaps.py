"""題型導向的證據缺口判定 —— 確定性規則，不是模型自評。

與 `tools.evidence_gap` 的關係：後者是「通用品質門檻」（四面向、時效、
獨立來源）；本模組加上**題型專屬的門檻** —— 假設驗證要反方、
比較分析要對稱。

為什麼不讓模型判斷「證據夠了嗎」：模型說夠了就停，等於把收斂條件
交給一個有動機提早結束的參與者。停止條件必須是外部規則。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from hoyabit_agent.domain import Asset, Evidence, Facet
from hoyabit_agent.question import EvidenceRequirement, QuestionType
from hoyabit_agent.reliability import ReliabilityTier, evidence_tier, weighted_source_count

STANCE_THRESHOLD = 0.15
"""與 tools.py 一致 —— 方向判定的死區，避免雜訊被當成表態。"""


class GapKind(str):
    """缺口種類的字串常數。用字串而非列舉，因為它們要原樣進 JSON 軌跡。"""


MISSING_FACETS = "missing_facets"
INSUFFICIENT_SOURCES = "insufficient_independent_sources"
MISSING_SUPPORTING = "missing_supporting_evidence"
MISSING_OPPOSING = "missing_opposing_evidence"
ASYMMETRIC_COMPARISON = "asymmetric_comparison"
LOW_QUALITY_ONLY = "low_reliability_sources_only"
UNRESOLVED_CONTRADICTION = "unresolved_contradiction"


@dataclass(frozen=True)
class Gap:
    """一個具名的證據缺口。`severity` 決定它能不能阻止收斂。"""

    kind: str
    severity: str  # "high" 阻止收斂；"low" 只寫進限制說明
    detail: str

    @property
    def blocking(self) -> bool:
        return self.severity == "high"


@dataclass(frozen=True)
class GapAssessment:
    """一輪 gap check 的完整結果。整份寫進 Execution Log。"""

    gaps: tuple[Gap, ...]
    missing_facets: frozenset[Facet]
    weighted_sources: float
    supporting_count: int
    opposing_count: int
    per_asset_facets: dict[str, int] = field(default_factory=dict)
    tier_counts: dict[str, int] = field(default_factory=dict)

    @property
    def blocking_gaps(self) -> tuple[Gap, ...]:
        return tuple(gap for gap in self.gaps if gap.blocking)

    def __bool__(self) -> bool:
        """真值即「還有阻止收斂的缺口」—— 讓 `if not assessment` 讀起來自然。"""
        return bool(self.blocking_gaps)

    def reasons(self) -> tuple[str, ...]:
        return tuple(gap.kind for gap in self.gaps)

    def describe(self) -> str:
        """給模型看的缺口敘述 —— 直接指出「還缺什麼、為什麼」。"""
        if not self.gaps:
            return "所有證據門檻皆已滿足。"
        # 刻意用純文字標記而非 emoji：這段字串會流進提示詞、推論軌跡與
        # Execution Log，而 Log 可能被寫到非 UTF-8 的終端或檔案。
        lines = []
        for gap in self.gaps:
            mark = "[必補]" if gap.blocking else "[限制]"
            lines.append(f"{mark} {gap.kind}: {gap.detail}")
        return "\n".join(lines)


def assess(
    evidence: tuple[Evidence, ...],
    requirement: EvidenceRequirement,
) -> GapAssessment:
    """依題型需求判定缺口。

    這是蒐集迴圈的**唯一**停止判準來源。模型可以建議還缺什麼主題，
    但不能宣告完成。
    """
    gaps: list[Gap] = []

    facet_counts = Counter(item.facet for item in evidence)
    missing = frozenset(
        facet for facet in requirement.required_facets if facet_counts[facet] < 1
    )
    if missing:
        names = "、".join(sorted(f.value for f in missing))
        gaps.append(Gap(MISSING_FACETS, "high", f"尚未取得 {names} 面的證據"))

    weighted = weighted_source_count(evidence)
    if weighted < requirement.minimum_independent_sources:
        gaps.append(
            Gap(
                INSUFFICIENT_SOURCES,
                "high",
                f"加權獨立來源數 {weighted:.1f} 低於門檻 "
                f"{requirement.minimum_independent_sources}"
                "（社群與轉載來源權重較低，需補第一手或主流媒體來源）",
            )
        )

    supporting = sum(1 for item in evidence if item.stance_hint > STANCE_THRESHOLD)
    opposing = sum(1 for item in evidence if item.stance_hint < -STANCE_THRESHOLD)

    if requirement.require_both_directions:
        if supporting == 0:
            gaps.append(
                Gap(
                    MISSING_SUPPORTING,
                    "high",
                    "尚無支持該說法的證據 —— 假設驗證題必須呈現正方",
                )
            )
        if opposing == 0:
            gaps.append(
                Gap(
                    MISSING_OPPOSING,
                    "high",
                    "尚無反對該說法的證據 —— 假設驗證題必須主動搜尋反方"
                    "（建議以「風險」「利空」「疑慮」等詞查新聞）",
                )
            )
    elif evidence and (supporting == 0 or opposing == 0):
        # 非假設驗證題不強制反方，但一面倒仍要寫進限制說明。
        side = "反方" if opposing == 0 else "正方"
        gaps.append(
            Gap(MISSING_OPPOSING if opposing == 0 else MISSING_SUPPORTING, "low",
                f"目前證據缺乏{side}，報告須明確揭露此限制")
        )

    per_asset = _facets_per_asset(evidence, requirement.assets)
    if requirement.require_symmetric_coverage and len(requirement.assets) >= 2:
        counts = [per_asset.get(a.value, 0) for a in requirement.assets]
        if min(counts) == 0 or max(counts) - min(counts) >= 2:
            detail = "、".join(
                f"{a.value}={per_asset.get(a.value, 0)} 面" for a in requirement.assets
            )
            gaps.append(
                Gap(
                    ASYMMETRIC_COMPARISON,
                    "high",
                    f"兩標的證據覆蓋不對稱（{detail}）—— 比較結論需要對齊的粒度",
                )
            )

    tiers = Counter(evidence_tier(item) for item in evidence)
    if evidence and tiers[ReliabilityTier.HIGH] == 0:
        gaps.append(
            Gap(
                LOW_QUALITY_ONLY,
                "high",
                "尚無高可信度證據（交易所原始數值或官方公告）—— "
                "僅憑媒體轉述不足以支撐數值型判斷",
            )
        )

    contradictions = _contradiction_facets(evidence)
    if contradictions:
        names = "、".join(sorted(f.value for f in contradictions))
        gaps.append(
            Gap(
                UNRESOLVED_CONTRADICTION,
                "low",
                f"{names} 面內部訊號矛盾 —— 報告須說明採信哪一側及理由",
            )
        )

    return GapAssessment(
        gaps=tuple(gaps),
        missing_facets=missing,
        weighted_sources=weighted,
        supporting_count=supporting,
        opposing_count=opposing,
        per_asset_facets=per_asset,
        tier_counts={tier.value: tiers[tier] for tier in ReliabilityTier},
    )


def _facets_per_asset(
    evidence: tuple[Evidence, ...], assets: tuple[Asset, ...]
) -> dict[str, int]:
    """每個標的各覆蓋了幾個證據面。

    標的歸屬由證據識別碼與摘要文字推斷 —— `Evidence` 刻意沒有 asset 欄位
    （證據面與資料源正交，標的資訊在識別碼裡），所以這裡用比對。
    """
    result: dict[str, int] = {}
    for asset in assets:
        token = asset.value.casefold()
        facets = {
            item.facet
            for item in evidence
            if token in item.id.casefold() or token in item.summary.casefold()
        }
        result[asset.value] = len(facets)
    return result


def _contradiction_facets(evidence: tuple[Evidence, ...]) -> frozenset[Facet]:
    """同一面內同時有明確正負訊號的那些面。"""
    grouped: dict[Facet, list[float]] = {facet: [] for facet in Facet}
    for item in evidence:
        grouped[item.facet].append(item.stance_hint)
    return frozenset(
        facet
        for facet, hints in grouped.items()
        if any(v > STANCE_THRESHOLD for v in hints)
        and any(v < -STANCE_THRESHOLD for v in hints)
    )


__all__ = [
    "ASYMMETRIC_COMPARISON",
    "INSUFFICIENT_SOURCES",
    "LOW_QUALITY_ONLY",
    "MISSING_FACETS",
    "MISSING_OPPOSING",
    "MISSING_SUPPORTING",
    "UNRESOLVED_CONTRADICTION",
    "Gap",
    "GapAssessment",
    "QuestionType",
    "assess",
]
