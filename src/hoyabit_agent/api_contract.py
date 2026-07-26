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

    # Build enhanced markdown report
    enhanced_md = _build_enhanced_markdown(outcome)

    return {
        "run_id": outcome.run_id,
        "asset": report.asset.value,
        "question": report.question,
        "stance": report.stance.value,
        "confidence": confidence.value if isinstance(confidence, Confidence) else None,
        "confidence_cause": (
            confidence.cause.value
            if not isinstance(confidence, Confidence)
            else None
        ),
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
        "enhanced_report_md": enhanced_md,
    }


def _build_enhanced_markdown(outcome: AnalysisOutcome) -> str:
    """Generate the enhanced markdown report with investor insights."""
    from hoyabit_agent.report_enhanced import enhanced_report_markdown

    try:
        return enhanced_report_markdown(outcome)
    except Exception:
        if outcome.report:
            return outcome.report.to_markdown()
        return ""


__all__ = ["outcome_payload"]
