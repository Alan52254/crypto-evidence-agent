"""競賽提交物輸出：Final Report、Evidence List、Execution Log、Manifest。"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hoyabit_agent.domain import AnalysisOutcome, Evidence, Report, Trace
from hoyabit_agent.trace_contract import execution_record, trace_node_record


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
        "event_key": item.event_key,
        "related_claim": claims,
        "sources": [
            {
                "source": excerpt.source_id,
                "source_url": excerpt.url,
                "fetched_at": excerpt.retrieved_at.isoformat(),
                "content_reference": {
                    "locator": excerpt.locator,
                    "excerpt": excerpt.text[:500],
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


def execution_log_jsonl(outcome: AnalysisOutcome) -> str:
    """產出競賽規格的 execution_log.jsonl（每行一個 JSON 事件）。"""
    lines: list[str] = []
    for node in outcome.trace.nodes:
        entry = {
            "event_id": f"{outcome.run_id}-{node.seq:03d}",
            "job_id": outcome.run_id,
            "timestamp": datetime.now(UTC).isoformat(),
            "node": node.kind.value,
            "event_type": node.kind.value,
            "duration_ms": round(node.elapsed_seconds * 1000),
            "status": "completed",
            "trace_id": outcome.run_id,
            "reason": node.reason,
            "evidence_ids": list(node.evidence_ids),
            "gap_before": sorted(f.value for f in node.gap_before),
            "gap_after": sorted(f.value for f in node.gap_after),
            "executions": [execution_record(ex) for ex in node.executions],
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines)


def output_manifest(outcome: AnalysisOutcome) -> dict[str, Any]:
    """產出 output manifest — 包含 SHA-256、版本資訊等。"""
    report = outcome.report
    report_md = report.to_markdown() if report else ""
    evidence_json = json.dumps(
        evidence_list(report) if report else [],
        ensure_ascii=False,
    )
    log_jsonl = execution_log_jsonl(outcome)

    return {
        "run_id": outcome.run_id,
        "generated_at": datetime.now(UTC).isoformat(),
        "artifacts": {
            "report": {
                "format": "markdown",
                "sha256": hashlib.sha256(report_md.encode()).hexdigest(),
                "size_bytes": len(report_md.encode()),
            },
            "evidence": {
                "format": "json",
                "sha256": hashlib.sha256(evidence_json.encode()).hexdigest(),
                "count": len(report.evidence) if report else 0,
            },
            "execution_log": {
                "format": "jsonl",
                "sha256": hashlib.sha256(log_jsonl.encode()).hexdigest(),
                "event_count": len(outcome.trace.nodes),
            },
        },
        "metadata": {
            "asset": report.asset.value if report else None,
            "stance": report.stance.value if report else None,
            "confidence": (
                report.confidence.value
                if report and hasattr(report.confidence, "value")
                else None
            ),
            "claims_kept": len(report.claims) if report else 0,
            "claims_dropped": len(report.dropped_claims) if report else 0,
            "total_evidence": len(report.evidence) if report else 0,
            "trace_nodes": len(outcome.trace.nodes),
        },
        "versions": {
            "agent": "0.1.0",
            "model": "gemini-3.6-flash",
            "python": "3.12",
        },
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


__all__ = ["evidence_list", "execution_log", "execution_log_jsonl", "output_manifest", "write_submission"]
