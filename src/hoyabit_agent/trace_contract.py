"""Canonical JSON contract shared by persistence, SSE, artifacts, and UI."""

from __future__ import annotations

from typing import Any

from hoyabit_agent.domain import ToolExecutionRecord, TraceNode


def execution_record(record: ToolExecutionRecord) -> dict[str, Any]:
    return {
        "tool": record.tool,
        "asset": record.asset.value,
        "arguments": dict(record.arguments),
        "status": record.status.value,
        "observation": record.observation,
        "evidence_ids": list(record.evidence_ids),
        "duration_seconds": record.duration_seconds,
    }


def trace_node_record(run_id: str, node: TraceNode) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "seq": node.seq,
        "kind": node.kind.value,
        "reason": node.reason,
        "evidence_ids": list(node.evidence_ids),
        "gap_before": sorted(facet.value for facet in node.gap_before),
        "gap_after": sorted(facet.value for facet in node.gap_after),
        "gap": dict(node.gap_state),
        "elapsed_seconds": node.elapsed_seconds,
        "executions": [execution_record(item) for item in node.executions],
    }


__all__ = ["execution_record", "trace_node_record"]
