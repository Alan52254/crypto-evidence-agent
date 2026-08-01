"""Function 工具 —— 行程內、確定性、無 I/O 的純計算。

依 I/O 邊界判準，這裡的一切都在線的「無 I/O」側，
因此**全部可在無任何 mock 的情況下測試**。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from hoyabit_agent.domain import (
    Asset,
    Claim,
    Confidence,
    ConfidenceResult,
    DraftClaim,
    Evidence,
    Facet,
    Figure,
    Insufficiency,
    InsufficientEvidence,
    Stance,
)

MINIMUM_EVIDENCE_PER_FACET = 1
MINIMUM_FACETS_FOR_CONFIDENCE = 2
STANCE_THRESHOLD = 0.15


def gate_asset(raw: str) -> Asset | None:
    """幣種閘門 —— 白名單比對，不是黑名單偵測。

    系統不判斷任何資產「是不是水幣」，只判斷它在不在受涵蓋幣種集合內。
    因此明天出現的新幣自動被擋，無需維護任何黑名單。
    """
    try:
        return Asset(raw.strip().upper())
    except ValueError:
        return None


def merge_independent_evidence(evidence: Iterable[Evidence]) -> tuple[Evidence, ...]:
    """同事件歸併 —— ADR 0002 的證據獨立性規則。

    同一則新聞被兩家媒體轉載不構成兩個獨立證據。歸併後**保留所有來源片段**
    （溯源不損失），但只計為一個證據。少了這一步，多加一個轉載型來源
    就能無成本地抬高信心度。
    """
    merged: dict[tuple[Facet, str], Evidence] = {}
    ordered: list[Evidence] = []

    for item in evidence:
        if item.event_key is None:
            ordered.append(item)
            continue

        key = (item.facet, item.event_key)
        existing = merged.get(key)
        if existing is None:
            merged[key] = item
            ordered.append(item)
            continue

        combined = Evidence(
            id=existing.id,
            facet=existing.facet,
            summary=existing.summary,
            stance_hint=(existing.stance_hint + item.stance_hint) / 2,
            excerpts=existing.excerpts + item.excerpts,
            event_key=existing.event_key,
            # 圖與來源片段同理：歸併不損失溯源，兩邊的圖都保留。
            figures=_distinct_figures(existing.figures, item.figures),
        )
        merged[key] = combined
        ordered[ordered.index(existing)] = combined

    # Tool calls remain lossless in the trace, while report evidence IDs are unique.
    # Repeated live observations keep the latest values and all distinct excerpts.
    by_id: dict[str, Evidence] = {}
    positions: list[str] = []
    for item in ordered:
        existing = by_id.get(item.id)
        if existing is None:
            by_id[item.id] = item
            positions.append(item.id)
            continue
        excerpts = tuple(dict.fromkeys((*existing.excerpts, *item.excerpts)))
        by_id[item.id] = Evidence(
            id=item.id,
            facet=item.facet,
            summary=item.summary,
            stance_hint=item.stance_hint,
            excerpts=excerpts,
            event_key=item.event_key or existing.event_key,
            figures=_distinct_figures(existing.figures, item.figures),
        )

    return tuple(by_id[evidence_id] for evidence_id in positions)


def _distinct_figures(*groups: tuple[Figure, ...]) -> tuple[Figure, ...]:
    """合併圖表並去重，保留先出現的順序。

    以呈現來源（`renderable_src`）判斷同一張圖：同一張 K 線圖被重複觀察
    兩次不該在報告裡出現兩次。
    """
    seen: dict[str, Figure] = {}
    for group in groups:
        for figure in group:
            seen.setdefault(figure.renderable_src, figure)
    return tuple(seen.values())


@dataclass(frozen=True)
class EvidenceGap:
    missing_facets: frozenset[Facet]
    direction_balance: bool
    contradiction_facets: frozenset[Facet]
    independent_sources: int
    fresh: bool
    reasons: tuple[str, ...]

    def __bool__(self) -> bool:
        return bool(self.reasons)

    def __iter__(self) -> Iterator[Facet]:
        return iter(self.missing_facets)


def evidence_gap(
    evidence: Iterable[Evidence],
    minimum_per_facet: int = MINIMUM_EVIDENCE_PER_FACET,
    *,
    now: datetime | None = None,
    max_age: timedelta = timedelta(hours=24),
) -> EvidenceGap:
    """Return all quality gates that can justify another evidence-gathering round."""
    items = tuple(evidence)
    counts = Counter(item.facet for item in items)
    missing = frozenset(facet for facet in Facet if counts[facet] < minimum_per_facet)
    positive = any(item.stance_hint > STANCE_THRESHOLD for item in items)
    negative = any(item.stance_hint < -STANCE_THRESHOLD for item in items)
    balanced = positive and negative
    grouped: dict[Facet, list[float]] = {facet: [] for facet in Facet}
    for item in items:
        grouped[item.facet].append(item.stance_hint)
    contradictions = frozenset(
        facet
        for facet, hints in grouped.items()
        if any(value > STANCE_THRESHOLD for value in hints)
        and any(value < -STANCE_THRESHOLD for value in hints)
    )
    sources = {
        excerpt.source_id
        for item in items
        for excerpt in item.excerpts
        if excerpt.source_id.strip()
    }
    reference = now or datetime.now(UTC)
    timestamps = [excerpt.retrieved_at for item in items for excerpt in item.excerpts]
    fresh = bool(timestamps) and max(timestamps) >= reference - max_age
    reasons: list[str] = []
    if missing:
        reasons.append("missing_facets")
    if not balanced:
        reasons.append("direction_imbalance")
    if len(sources) < 2:
        reasons.append("insufficient_independent_sources")
    if not fresh:
        reasons.append("stale_evidence")
    if contradictions:
        reasons.append("unresolved_contradiction")
    return EvidenceGap(missing, balanced, contradictions, len(sources), fresh, tuple(reasons))

def facet_stance(evidence: Iterable[Evidence]) -> Stance:
    """單一證據面的方向傾向。每個面必須能獨立產出傾向 —— 這是信心度的前提。

    特殊處理：sentiment 面的方向由**市場情緒指標**（如 FGI）主導，
    而非新聞文本語氣（NEWS-SENT）。原因：正面新聞（如升級公告）
    可以在恐懼市場中被報導，兩者不矛盾但不該被平均成一個方向。
    新聞語氣正面 ≠ 市場情緒正面。

    NEWS-SENT 項仍保留在 sentiment 面（用於覆蓋率、引用、呈現），
    但不參與方向投票。
    """
    items = list(evidence)
    if not items:
        return Stance.NEUTRAL

    # 區分「市場情緒指標」與「新聞語氣」
    # 市場情緒指標：FGI-*, 或其他非新聞來源的 sentiment evidence
    # 新聞語氣：NEWS-SENT-*, XNEWS-SENT-*
    market_sentiment_hints = []
    news_tone_hints = []

    for item in items:
        if item.id.startswith("NEWS-SENT-") or item.id.startswith("XNEWS-SENT-"):
            news_tone_hints.append(item.stance_hint)
        else:
            market_sentiment_hints.append(item.stance_hint)

    # 方向判定優先用市場情緒指標；若沒有市場指標才退回全體平均
    if market_sentiment_hints:
        hints = market_sentiment_hints
    else:
        # 沒有 FGI 等市場指標時，才用新聞語氣作為 fallback
        hints = [item.stance_hint for item in items]

    mean = sum(hints) / len(hints)
    if mean > STANCE_THRESHOLD:
        return Stance.BULLISH
    if mean < -STANCE_THRESHOLD:
        return Stance.BEARISH
    return Stance.NEUTRAL


def facets_with_evidence(evidence: Iterable[Evidence]) -> frozenset[Facet]:
    """實際蒐集到證據的證據面。與「有表態的面」是兩回事。"""
    return frozenset(item.facet for item in evidence)


def facet_stances(evidence: Iterable[Evidence]) -> dict[Facet, Stance]:
    """**全部四個面**的傾向，沒有證據的面為中性。

    刻意回傳四個而非只回傳有證據的面 —— ADR 0002 要求報告呈現全部四面，
    讀者才分得出「這一面沉默」與「這一面根本沒查」。
    只回傳有證據的面會讓報告出現兩列的表格，那個資訊是遺失的。
    """
    grouped: dict[Facet, list[Evidence]] = {facet: [] for facet in Facet}
    for item in evidence:
        grouped[item.facet].append(item)
    return {facet: facet_stance(items) for facet, items in grouped.items()}


def as_of_reference(as_of: date | datetime | None) -> datetime:
    """把分析截止日換成新鮮度的參考時刻 —— 該日的 UTC 收盤(23:59:59)。

    日 K 代表當日收盤時的資產狀態,因此「當日產生的證據」在該日評估時
    年齡應趨近零。未指定時退回現實時鐘,維持即時模式的既有行為。
    ADR 0005:時效永遠對 as_of 相減,不對現實時鐘。
    """
    if as_of is None:
        return datetime.now(UTC)
    if isinstance(as_of, datetime):
        return as_of
    return datetime(as_of.year, as_of.month, as_of.day, 23, 59, 59, tzinfo=UTC)


def assess_confidence(
    evidence: Iterable[Evidence],
    minimum_facets: int = MINIMUM_FACETS_FOR_CONFIDENCE,
    *,
    as_of: date | datetime | None = None,
    core_data_demands: tuple[object, ...] = (),
) -> ConfidenceResult:
    """信心度 — 多維度加權評估（ADR 0002 + 競賽規格）。

    權重分配：
    - 來源品質 (independence): 25%
    - 覆蓋 (coverage): 25%
    - 時效 (freshness): 20%
    - 一致性 (agreement): 20%
    - 完整性 (completeness): 10%

    核心資料缺失懲罰（A+B 策略）：
    - 題目核心需求不可用 (CORE + UNAVAILABLE): 每項 -0.15
    - 題目輔助需求不可用 (SUPPORTING + UNAVAILABLE): 每項 -0.08
    - 部分可用 (PARTIAL): 每項 -0.05
    若懲罰後分數低於 InsufficientEvidence 門檻，直接返回第三態。

    `as_of` 是新鮮度的參考時間立足點(見 ADR 0005)。未指定時退回現實時鐘。
    """
    items = tuple(evidence)
    stances = facet_stances(items)
    present = facets_with_evidence(items)

    if len(present) < minimum_facets:
        return InsufficientEvidence(
            facets_present=present,
            minimum_facets_required=minimum_facets,
            cause=Insufficiency.TOO_FEW_FACETS,
            facet_stances=stances,
        )

    directional = {
        facet: stance for facet, stance in stances.items() if stance is not Stance.NEUTRAL
    }
    if len(directional) < minimum_facets:
        return InsufficientEvidence(
            facets_present=present,
            minimum_facets_required=minimum_facets,
            cause=Insufficiency.NO_DIRECTIONAL_SIGNAL,
            directional_facets=frozenset(directional),
            facet_stances=stances,
        )

    # 1. Agreement (一致性) — 20%
    counts = Counter(directional.values())
    majority_stance = counts.most_common(1)[0][0]
    raw_agreement = counts.most_common(1)[0][1] / len(directional)
    # 表態面數量折扣：2面一致 ≠ 4面一致。只有2面表態時打五折。
    directional_ratio = len(directional) / len(Facet)
    # 面向矛盾懲罰：若有面跟多數方向不一致，每個矛盾面扣 0.15
    contradicting = sum(1 for s in directional.values() if s != majority_stance)
    contradiction_penalty = contradicting * 0.15
    agreement = max(0.0, raw_agreement * directional_ratio - contradiction_penalty)

    # 2. Coverage (覆蓋) — 25%: proportion of 4 facets that have evidence
    coverage = len(present) / len(Facet)

    # 可回答性懲罰：如果技術面只有過期 dataset 資料（沒有即時 Binance spot），
    # coverage 打折 —— 代表「有證據但品質不足以回答即時問題」
    tech_sources = [
        excerpt.source_id
        for item in items if item.facet is Facet.TECHNICAL
        for excerpt in item.excerpts
    ]
    has_live_technical = any(s.startswith("BNC-SPOT") for s in tech_sources)
    has_only_dataset = all(s.startswith("dataset") for s in tech_sources) if tech_sources else False
    if has_only_dataset and not has_live_technical and Facet.TECHNICAL in present:
        coverage *= 0.6  # 過期技術面只算 60% 的覆蓋

    # 3. Independence (來源品質) — 25%
    # 以可信度加權、且**同事件合併**的來源數。直接數 distinct source_id 會讓
    # 「多找一家轉載」無成本地抬高信心度 —— 那正是 ADR 0002 要堵的漏洞。
    from hoyabit_agent.reliability import weighted_source_count

    independence = min(weighted_source_count(items), 6.0) / 6

    # 4. Freshness (時效) — 20%: based on most recent evidence timestamp
    timestamps = [excerpt.retrieved_at for item in items for excerpt in item.excerpts]
    if timestamps:
        newest = max(timestamps)
        age = as_of_reference(as_of) - newest
        # Fresh within 1 hour = 1.0, decays over 24 hours to 0.2
        if age <= timedelta(hours=1):
            freshness = 1.0
        elif age <= timedelta(hours=24):
            freshness = max(0.2, 1.0 - (age.total_seconds() / (24 * 3600)) * 0.8)
        else:
            freshness = 0.2
    else:
        freshness = 0.2

    # 5. Completeness (完整性) — 10%: evidence count relative to minimum
    total_evidence = len(items)
    completeness = min(total_evidence, 15) / 15  # Cap at 15 items for full score

    # Weighted combination
    value = (
        independence * 0.25
        + coverage * 0.25
        + freshness * 0.20
        + agreement * 0.20
        + completeness * 0.10
    )

    # ─── 核心資料缺失懲罰（A+B 策略）───
    # 題目明確要求但系統不具備的資料類型，直接下修信心度。
    # 這不是「分析品質差」，而是「根本沒有回答主問題所需的資料」。
    from hoyabit_agent.question import CoreDataDemand, DataAvailability, DemandWeight

    core_penalty = 0.0
    for demand in core_data_demands:
        if not isinstance(demand, CoreDataDemand):
            continue
        if demand.availability is DataAvailability.UNAVAILABLE:
            if demand.weight is DemandWeight.CORE:
                core_penalty += 0.15  # 題目主問的東西完全沒有
            else:
                core_penalty += 0.08  # 輔助資料缺失
        elif demand.availability is DataAvailability.PARTIAL:
            core_penalty += 0.05  # 只有間接替代

    value = max(0.0, value - core_penalty)

    # 若懲罰後分數過低（< 0.25），且有核心需求不可用，
    # 直接回傳 InsufficientEvidence —— 這不是「低信心」而是「無法回答」。
    has_core_unavailable = any(
        isinstance(d, CoreDataDemand)
        and d.availability is DataAvailability.UNAVAILABLE
        and d.weight is DemandWeight.CORE
        for d in core_data_demands
    )
    if has_core_unavailable and value < 0.25:
        return InsufficientEvidence(
            facets_present=present,
            minimum_facets_required=minimum_facets,
            cause=Insufficiency.NO_DIRECTIONAL_SIGNAL,
            directional_facets=frozenset(directional),
            facet_stances=stances,
        )

    return Confidence(
        value=round(value, 4),
        facet_stances=dict(stances),
        independence=round(independence, 4),
        coverage=round(coverage, 4),
        freshness=round(freshness, 4),
        agreement=round(agreement, 4),
        completeness=round(completeness, 4),
    )


def apply_contested_penalty(
    confidence: ConfidenceResult,
    *,
    supported_count: int,
    contested_count: int,
    total_claims: int,
) -> tuple[ConfidenceResult, dict[str, float]]:
    """依判斷爭議比例修正信心度。回傳 (adjusted_confidence, penalty_info)。

    penalty_info 包含 contested_ratio、penalty_value 等，供 trace log 使用。
    若不需要修正（比例 <= 50% 或判斷數不足），回傳原始 confidence 與空 dict。

    設計：此函式與 assess_confidence 刻意分開 —— assess_confidence 只看
    「證據本身的品質」，本函式看「模型產出判斷的品質」。兩者測量不同東西，
    但共同決定最終信心度。分開讓各自可獨立測試。
    """
    if not isinstance(confidence, Confidence) or total_claims < 3:
        return confidence, {}

    contested_ratio = contested_count / total_claims
    if contested_ratio <= 0.5:
        return confidence, {}

    # 每超出 50% 一個百分點，扣 0.3 個百分點
    penalty = (contested_ratio - 0.5) * 0.30
    adjusted = max(0.0, confidence.value - penalty)

    adjusted_confidence = Confidence(
        value=round(adjusted, 4),
        facet_stances=confidence.facet_stances,
        independence=confidence.independence,
        coverage=confidence.coverage,
        freshness=confidence.freshness,
        agreement=confidence.agreement,
        completeness=confidence.completeness,
    )

    info = {
        "supported": supported_count,
        "contested": contested_count,
        "total_claims": total_claims,
        "contested_ratio": contested_ratio,
        "original_confidence": confidence.value,
        "penalty": penalty,
        "adjusted_confidence": adjusted,
    }

    return adjusted_confidence, info


def overall_stance(confidence: ConfidenceResult) -> Stance:
    """報告的整體方向。

    只由**有表態的面**投票 —— 沉默的面不該被當成一張「中性票」而稀釋結果。
    表態的面平手時為中性，不強行選邊。

    額外規則：若技術面與籌碼面（價格直接相關的面）皆為中性，
    即使基本面/情緒面偏多或偏空，整體方向仍判中性 ——
    因為新聞利多但價格不動，不構成「偏多」的方向性判斷。
    """
    if isinstance(confidence, InsufficientEvidence):
        return Stance.NEUTRAL

    stances = confidence.facet_stances

    # 價格面（技術 + 籌碼）是否都沒有明確方向
    price_facets = (Facet.TECHNICAL, Facet.POSITIONING)
    price_directional = any(
        stances.get(f, Stance.NEUTRAL) is not Stance.NEUTRAL for f in price_facets
    )

    directional = [
        stance for stance in stances.values() if stance is not Stance.NEUTRAL
    ]
    if not directional:
        return Stance.NEUTRAL

    # 如果價格面都沒表態，即使新聞面偏多/偏空，整體方向回中性
    if not price_directional:
        return Stance.NEUTRAL

    ranked = Counter(directional).most_common()
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return Stance.NEUTRAL
    return ranked[0][0]


def check_citations(
    drafts: Iterable[DraftClaim],
    evidence: Iterable[Evidence],
) -> tuple[tuple[Claim, ...], tuple[DraftClaim, ...]]:
    """引用檢核 —— 對結構化判斷陣列過濾。

    未掛載證據、或掛載了不存在的證據識別碼者一律丟棄。
    這是對物件陣列的過濾，不是對散文剪裁句子 ——
    後者會造成主語遺失與語法崩潰，且不可靠。
    """
    known = {item.id for item in evidence}
    kept: list[Claim] = []
    dropped: list[DraftClaim] = []

    for draft in drafts:
        cited = tuple(eid for eid in draft.evidence_ids if eid in known)
        if cited:
            kept.append(
                Claim(
                    text=draft.text,
                    evidence_ids=cited,
                    facet=draft.facet,
                    role=draft.role,
                )
            )
        else:
            dropped.append(draft)

    return tuple(kept), tuple(dropped)


__all__ = [
    "as_of_reference",
    "assess_confidence",
    "check_citations",
    "evidence_gap",
    "facet_stance",
    "facet_stances",
    "facets_with_evidence",
    "gate_asset",
    "merge_independent_evidence",
    "overall_stance",
]
