"""Function 工具 —— 行程內、確定性、無 I/O 的純計算。

依 I/O 邊界判準，這裡的一切都在線的「無 I/O」側，
因此**全部可在無任何 mock 的情況下測試**。
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

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

    return tuple(ordered)


def evidence_gap(
    evidence: Iterable[Evidence],
    minimum_per_facet: int = MINIMUM_EVIDENCE_PER_FACET,
) -> frozenset[Facet]:
    """證據缺口 —— 尚未蒐集到足夠證據的證據面。

    這是蒐集迴圈的終止條件，也是模型決定下一步的依據。把「模型該做什麼」
    從一段模糊的 prompt 變成一個可計算、可顯示、可斷言的狀態。
    """
    counts = Counter(item.facet for item in evidence)
    return frozenset(facet for facet in Facet if counts[facet] < minimum_per_facet)


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


def assess_confidence(
    evidence: Iterable[Evidence],
    minimum_facets: int = MINIMUM_FACETS_FOR_CONFIDENCE,
) -> ConfidenceResult:
    """信心度 —— 證據面之間的一致程度（ADR 0002）。

    輸入必須是**歸併後**的證據。

    有兩種算不出來的情況，兩者都回傳 `InsufficientEvidence` 而非高信心度：

    1. 蒐集到的證據面少於 `minimum_facets` —— 否則「只查到一則新聞 →
       四面一致 → 高信心」這種荒謬結果會發生。
    2. 面夠多但**表態的面**不足 —— 一致程度只在有方向的面之間才有意義。
       三個面沉默、一個面看空，那不是「75% 共識」，那是沒有訊號。
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

    counts = Counter(directional.values())
    agreement = counts.most_common(1)[0][1] / len(directional)
    # 回報**所有**面的傾向（含中性），讀者才看得出誰沉默、誰表態。
    return Confidence(value=agreement, facet_stances=dict(stances))


def overall_stance(confidence: ConfidenceResult) -> Stance:
    """報告的整體方向。

    只由**有表態的面**投票 —— 沉默的面不該被當成一張「中性票」而稀釋結果。
    表態的面平手時為中性，不強行選邊。
    """
    if isinstance(confidence, InsufficientEvidence):
        return Stance.NEUTRAL

    directional = [
        stance for stance in confidence.facet_stances.values() if stance is not Stance.NEUTRAL
    ]
    if not directional:
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
            kept.append(Claim(text=draft.text, evidence_ids=cited, facet=draft.facet))
        else:
            dropped.append(draft)

    return tuple(kept), tuple(dropped)


__all__ = [
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
