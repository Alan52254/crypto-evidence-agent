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
        )

    return tuple(by_id[evidence_id] for evidence_id in positions)


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
    """單一證據面的方向傾向。每個面必須能獨立產出傾向 —— 這是信心度的前提。"""
    hints = [item.stance_hint for item in evidence]
    if not hints:
        return Stance.NEUTRAL
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
) -> ConfidenceResult:
    """信心度 — 多維度加權評估（ADR 0002 + 競賽規格）。

    權重分配：
    - 來源品質 (independence): 25%
    - 覆蓋 (coverage): 25%
    - 時效 (freshness): 20%
    - 一致性 (agreement): 20%
    - 完整性 (completeness): 10%

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
    raw_agreement = counts.most_common(1)[0][1] / len(directional)
    # 表態面數量折扣：2面一致 ≠ 4面一致。只有2面表態時打五折。
    directional_ratio = len(directional) / len(Facet)
    agreement = raw_agreement * directional_ratio

    # 2. Coverage (覆蓋) — 25%: proportion of 4 facets that have evidence
    coverage = len(present) / len(Facet)

    # 3. Independence (來源品質) — 25%
    # 以可信度加權、且**同事件合併**的來源數。直接數 distinct source_id 會讓
    # 「多找一家轉載」無成本地抬高信心度 —— 那正是 ADR 0002 要堵的漏洞。
    from hoyabit_agent.reliability import weighted_source_count

    independence = min(weighted_source_count(items), 4.0) / 4

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

    return Confidence(
        value=round(value, 4),
        facet_stances=dict(stances),
        independence=round(independence, 4),
        coverage=round(coverage, 4),
        freshness=round(freshness, 4),
        agreement=round(agreement, 4),
        completeness=round(completeness, 4),
    )


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
