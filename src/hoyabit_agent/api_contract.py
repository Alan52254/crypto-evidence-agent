"""HTTP payload assembly kept separate from the transport layer."""

from __future__ import annotations

from typing import Any

from hoyabit_agent.artifacts import evidence_list
from hoyabit_agent.domain import AnalysisOutcome, Confidence


def outcome_payload(outcome: AnalysisOutcome) -> dict[str, Any]:
    report = outcome.report
    if report is None:
        return {
            "run_id": outcome.run_id,
            "report": None,
            "rejection": outcome.rejection.reason if outcome.rejection else "unknown",
        }
    confidence = report.confidence
    return {
        "run_id": outcome.run_id,
        "asset": report.asset.value,
        "question": report.question,
        "stance": report.stance.value,
        "confidence": confidence.value if isinstance(confidence, Confidence) else None,
        "cutoff": "2026-05-31 UTC",
        "facet_stances": {
            facet.value: stance.value for facet, stance in confidence.facet_stances.items()
        },
        "claims": [
            {
                "text": claim.text,
                "evidence_ids": list(claim.evidence_ids),
                "facet": claim.facet.value,
                "role": claim.role.value,
            }
            for claim in report.claims
        ],
        "evidence": evidence_list(report),
    }


__all__ = ["outcome_payload"]
