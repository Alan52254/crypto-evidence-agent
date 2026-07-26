"""判斷帳本 —— 把「形成判斷」與「驗證判斷」拆成兩個階段。

為什麼要拆：若同一次模型呼叫同時看證據、下判斷、又自我驗證，
那是自我確認偏誤的教科書條件。驗證必須由**不參與撰寫的規則**執行。

因此本模組的驗證刻意是確定性的：檢查引用是否存在、來源層級是否足夠、
結論是否有足夠獨立支撐、所屬面是否存在矛盾。這些都可以在無 mock、
無網路的情況下完整測試。

與 `tools.check_citations` 的關係：後者只做「有沒有掛到存在的證據」
這個二元閘門。本模組把它擴充成三態，並保留被拒絕的判斷 ——
命題要求說明不確定性，被丟棄的判斷正是不確定性的最佳來源。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum

from hoyabit_agent.domain import ClaimRole, DraftClaim, Evidence, Facet
from hoyabit_agent.reliability import ReliabilityTier, evidence_tier

STANCE_THRESHOLD = 0.15
MINIMUM_EVIDENCE_FOR_CONCLUSION = 2
"""結論層的判斷需要至少兩項獨立證據 —— 單一證據撐不起一個結論。"""


class ClaimStatus(Enum):
    """判斷的驗證結果。三態，不是二元。"""

    SUPPORTED = "supported"
    """引用有效、來源品質足夠、無未處理的矛盾。可進報告主體。"""

    CONTESTED = "contested"
    """引用有效，但支撐薄弱或存在矛盾訊號。可進報告，**必須揭露爭議**。"""

    UNSUPPORTED = "unsupported"
    """引用無效或不存在。不得進報告主體，轉為限制說明。"""


@dataclass(frozen=True)
class VerifiedClaim:
    """經過驗證的判斷，帶狀態與驗證理由。

    `reasons` 是驗證器留下的痕跡 —— 報告要能說明「為什麼這則被標為爭議」，
    而不是只給一個狀態字。
    """

    text: str
    evidence_ids: tuple[str, ...]
    facet: Facet
    role: ClaimRole
    status: ClaimStatus
    reasons: tuple[str, ...] = ()
    dropped_evidence_ids: tuple[str, ...] = ()
    """模型引用了但實際不存在的識別碼 —— 幻覺的直接證據，值得留存。"""

    @property
    def admissible(self) -> bool:
        """能否進入報告主體。"""
        return self.status is not ClaimStatus.UNSUPPORTED


@dataclass(frozen=True)
class LedgerResult:
    """一次驗證的完整結果。"""

    claims: tuple[VerifiedClaim, ...]

    @property
    def supported(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.SUPPORTED)

    @property
    def contested(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.CONTESTED)

    @property
    def unsupported(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.status is ClaimStatus.UNSUPPORTED)

    @property
    def admissible(self) -> tuple[VerifiedClaim, ...]:
        return tuple(c for c in self.claims if c.admissible)

    def limitations(self) -> tuple[str, ...]:
        """把被拒絕與有爭議的判斷轉譯成限制說明。

        這是本模組存在的主要理由：命題要求「資料不足、訊號矛盾、
        來源可信度有限時明確指出限制」。被靜默丟棄的判斷等於丟掉了
        這些分數。
        """
        lines: list[str] = []
        for claim in self.unsupported:
            why = "；".join(claim.reasons) or "引用無效"
            lines.append(f"未能驗證的說法：{claim.text}（{why}）")
        for claim in self.contested:
            why = "；".join(claim.reasons)
            lines.append(f"支撐薄弱的判斷：{claim.text}（{why}）")
        return tuple(lines)


def verify(
    drafts: tuple[DraftClaim, ...],
    evidence: tuple[Evidence, ...],
) -> LedgerResult:
    """驗證一批草稿判斷。純函數，無 I/O，無模型呼叫。

    驗證順序刻意是「引用存在 → 來源品質 → 支撐強度 → 矛盾」：
    引用不存在時後面的檢查都沒有意義。
    """
    by_id = {item.id: item for item in evidence}
    contradiction_facets = _contradiction_facets(evidence)

    verified: list[VerifiedClaim] = []
    for draft in drafts:
        cited = tuple(eid for eid in draft.evidence_ids if eid in by_id)
        hallucinated = tuple(eid for eid in draft.evidence_ids if eid not in by_id)
        reasons: list[str] = []

        if not cited:
            if hallucinated:
                reasons.append(
                    f"引用了不存在的證據識別碼：{', '.join(hallucinated)}"
                )
            else:
                reasons.append("未掛載任何證據")
            verified.append(
                VerifiedClaim(
                    text=draft.text,
                    evidence_ids=(),
                    facet=draft.facet,
                    role=draft.role,
                    status=ClaimStatus.UNSUPPORTED,
                    reasons=tuple(reasons),
                    dropped_evidence_ids=hallucinated,
                )
            )
            continue

        if hallucinated:
            reasons.append(
                f"部分引用無效已剔除：{', '.join(hallucinated)}"
            )

        cited_evidence = tuple(by_id[eid] for eid in cited)
        status = ClaimStatus.SUPPORTED

        tiers = [evidence_tier(item) for item in cited_evidence]
        if all(tier is ReliabilityTier.LOW for tier in tiers):
            status = ClaimStatus.CONTESTED
            reasons.append("僅由低可信度來源支撐")

        if (
            draft.role is ClaimRole.CONCLUSION
            and len(_independent_sources(cited_evidence)) < MINIMUM_EVIDENCE_FOR_CONCLUSION
        ):
            status = ClaimStatus.CONTESTED
            reasons.append(
                f"結論層判斷僅有 {len(_independent_sources(cited_evidence))} 個獨立來源"
                f"，低於 {MINIMUM_EVIDENCE_FOR_CONCLUSION}"
            )

        if draft.facet in contradiction_facets:
            status = ClaimStatus.CONTESTED
            reasons.append(f"{draft.facet.value} 面存在方向矛盾的證據")

        if _cited_directions_conflict(cited_evidence):
            status = ClaimStatus.CONTESTED
            reasons.append("所引證據彼此方向相反")

        verified.append(
            VerifiedClaim(
                text=draft.text,
                evidence_ids=cited,
                facet=draft.facet,
                role=draft.role,
                status=status,
                reasons=tuple(reasons),
                dropped_evidence_ids=hallucinated,
            )
        )

    return LedgerResult(claims=tuple(verified))


def coverage_ratio(result: LedgerResult) -> float:
    """關鍵結論的證據覆蓋率 —— spec 的成功標準之一。

    只看結論層：事實層本來就是引述證據，把它算進去會虛高。
    """
    conclusions = [c for c in result.claims if c.role is ClaimRole.CONCLUSION]
    if not conclusions:
        return 1.0
    covered = sum(1 for c in conclusions if c.status is not ClaimStatus.UNSUPPORTED)
    return covered / len(conclusions)


def role_breakdown(result: LedgerResult) -> dict[str, int]:
    """各層判斷的數量 —— 檢查報告是否真的有三層結構。"""
    counts = Counter(c.role.value for c in result.admissible)
    return {role.value: counts.get(role.value, 0) for role in ClaimRole}


def _independent_sources(evidence: tuple[Evidence, ...]) -> frozenset[str]:
    return frozenset(
        excerpt.source_id
        for item in evidence
        for excerpt in item.excerpts
        if excerpt.source_id.strip()
    )


def _contradiction_facets(evidence: tuple[Evidence, ...]) -> frozenset[Facet]:
    grouped: dict[Facet, list[float]] = {facet: [] for facet in Facet}
    for item in evidence:
        grouped[item.facet].append(item.stance_hint)
    return frozenset(
        facet
        for facet, hints in grouped.items()
        if any(v > STANCE_THRESHOLD for v in hints)
        and any(v < -STANCE_THRESHOLD for v in hints)
    )


def _cited_directions_conflict(cited: tuple[Evidence, ...]) -> bool:
    positive = any(item.stance_hint > STANCE_THRESHOLD for item in cited)
    negative = any(item.stance_hint < -STANCE_THRESHOLD for item in cited)
    return positive and negative


__all__ = [
    "ClaimStatus",
    "LedgerResult",
    "VerifiedClaim",
    "coverage_ratio",
    "role_breakdown",
    "verify",
]
