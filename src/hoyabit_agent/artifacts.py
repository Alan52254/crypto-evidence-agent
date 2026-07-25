"""競賽提交物輸出：Final Report、Evidence List、Execution Log。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from hoyabit_agent.domain import AnalysisOutcome, Evidence, Report, Trace
from hoyabit_agent.trace_contract import trace_node_record


def evidence_list(report: Report) -> list[dict[str, Any]]:
    related: dict[str, list[str]] = {}
    for claim in report.claims:
        for evidence_id in claim.evidence_ids:
            related.setdefault(evidence_id, []).append(claim.text)
    return [_evidence_record(item, related.get(item.id, [])) for item in report.evidence]


def _evidence_record(item: Evidence, claims: list[str]) -> dict[str, Any]:
    return {
        "evidence_id": item.id,
        "facet": item.facet.value,
        "summary": item.summary,
        "stance_hint": item.stance_hint,
        "related_claim": claims,
        "sources": [
            {
                "source": excerpt.source_id,
                "source_url": excerpt.url,
                "fetched_at": excerpt.retrieved_at.isoformat(),
                "content_reference": {
                    "locator": excerpt.locator,
                    "excerpt": excerpt.text,
                },
            }
            for excerpt in item.excerpts
        ],
    }


def execution_log(trace: Trace) -> dict[str, Any]:
    return {
        "run_id": trace.run_id,
        "nodes": [trace_node_record(trace.run_id, node) for node in trace.nodes],
    }

def write_submission(outcome: AnalysisOutcome, output_dir: Path) -> tuple[Path, ...]:
    run_dir = output_dir / outcome.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "final_report.md"
    evidence_path = run_dir / "evidence_list.json"
    log_path = run_dir / "execution_log.json"

    if outcome.report is None:
        reason = outcome.rejection.reason if outcome.rejection else "未知原因"
        report_path.write_text(f"# 分析遭拒\n\n{reason}\n", encoding="utf-8")
        evidence_payload: list[dict[str, Any]] = []
    else:
        report_path.write_text(outcome.report.to_markdown() + "\n", encoding="utf-8")
        evidence_payload = evidence_list(outcome.report)

    evidence_path.write_text(
        json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    log_path.write_text(
        json.dumps(execution_log(outcome.trace), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report_path, evidence_path, log_path


__all__ = ["evidence_list", "execution_log", "write_submission"]
