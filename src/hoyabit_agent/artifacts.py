"""競賽提交物輸出：Final Report、Evidence List、Execution Log、Manifest。"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from hoyabit_agent.domain import AnalysisOutcome, Evidence, Report, SourceExcerpt, Trace
from hoyabit_agent.reliability import ReliabilityTier, tier_for_source
from hoyabit_agent.trace_contract import execution_record, trace_node_record

# 版本定義 — 每次改動 prompts 或公式時手動更新
PROMPT_VERSION = "v1.4.0"
FORMULA_VERSION = "v1.0.0"


def evidence_list(report: Report) -> list[dict[str, Any]]:
    related: dict[str, list[str]] = {}
    for claim in report.claims:
        for evidence_id in claim.evidence_ids:
            related.setdefault(evidence_id, []).append(claim.text)
    return [_evidence_record(item, related.get(item.id, [])) for item in report.evidence]


def _evidence_record(item: Evidence, claims: list[str]) -> dict[str, Any]:
    primary = item.excerpts[0] if item.excerpts else None
    source_id = primary.source_id if primary else ""
    scores = _compute_scores(item, primary)

    return {
        "evidence_id": item.id,
        "facet": item.facet.value,
        "summary": item.summary,
        "stance_hint": item.stance_hint,
        "event_key": item.event_key,
        "source": source_id,
        "source_url": primary.url if primary else None,
        "source_type": _infer_source_type(source_id),
        "fetched_at": primary.retrieved_at.isoformat() if primary and primary.retrieved_at else None,
        "content_reference": {
            "locator": primary.locator if primary else "",
            "excerpt": primary.text[:500] if primary else "",
        },
        "query_context": _infer_query_context(item),
        "related_claim": claims,
        "figures": [
            {
                "kind": figure.kind.value,
                "caption": figure.caption,
                "src": figure.renderable_src,
                "source_url": figure.source_url,
                "alt": figure.alt,
            }
            for figure in item.figures
        ],
        "raw_artifact_uri": None,
        "sha256": _compute_sha256(primary) if primary else None,
        "quality_score": scores["quality"],
        "relevance_score": scores["relevance"],
        "freshness_score": scores["freshness"],
        "independence_score": scores["independence"],
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


def _compute_sha256(excerpt: SourceExcerpt) -> str:
    """對 excerpt 的原始文字 bytes 計算 SHA-256。"""
    content = excerpt.text.encode("utf-8") if excerpt.text else b""
    return hashlib.sha256(content).hexdigest()


def _infer_source_type(source_id: str) -> str:
    """從 source_id 前綴推斷來源類型。"""
    lowered = source_id.casefold()
    if lowered.startswith("bnc-") or lowered.startswith("binance"):
        return "api"
    if lowered.startswith("dataset"):
        return "csv"
    if lowered.startswith("official") or lowered.startswith("ethereum-blog"):
        return "official"
    if any(lowered.startswith(p) for p in ("coindesk", "cointelegraph", "blocktempo", "blockworks")):
        return "rss"
    return "unknown"


def _infer_query_context(item: Evidence) -> dict[str, Any]:
    """從 evidence ID 和來源推斷查詢的工具名稱和參數。"""
    eid = item.id
    source_id = item.excerpts[0].source_id if item.excerpts else ""

    if eid.startswith("BNC-SPOT-"):
        parts = eid.replace("BNC-SPOT-", "").split("-")
        asset = parts[0] if parts else ""
        interval = parts[1] if len(parts) > 1 else "1d"
        return {"tool_name": "binance_spot", "arguments": {"asset": asset, "interval": interval}}
    if eid.startswith("BNC-PERP-"):
        parts = eid.replace("BNC-PERP-", "").split("-")
        asset = parts[0] if parts else ""
        return {"tool_name": "binance_derivatives", "arguments": {"asset": asset}}
    if eid.startswith("MARKET-"):
        asset = eid.replace("MARKET-", "").split("-")[0]
        return {"tool_name": "market_dataset_context", "arguments": {"asset": asset}}
    if source_id.startswith("official") or source_id.startswith("ethereum-blog"):
        return {"tool_name": "official_announcements", "arguments": {}}
    if any(source_id.startswith(p) for p in ("coindesk", "cointelegraph")):
        return {"tool_name": "crypto_news", "arguments": {}}
    if any(source_id.startswith(p) for p in ("blocktempo", "blockworks")):
        return {"tool_name": "extended_news", "arguments": {}}
    return {"tool_name": "unknown", "arguments": {}}


def _compute_scores(item: Evidence, excerpt: SourceExcerpt | None) -> dict[str, float]:
    """計算四維品質分數（formula_version v1.0.0）。"""
    source_id = excerpt.source_id if excerpt else ""
    tier = tier_for_source(source_id)
    quality = {ReliabilityTier.HIGH: 0.95, ReliabilityTier.MEDIUM: 0.70, ReliabilityTier.LOW: 0.40}[tier]
    relevance = min(1.0, 0.5 + abs(item.stance_hint) * 0.5)

    freshness = 0.5
    if excerpt and excerpt.retrieved_at:
        delta_hours = (datetime.now(UTC) - excerpt.retrieved_at).total_seconds() / 3600
        freshness = max(0.01, min(1.0, math.exp(-delta_hours / 24.0)))

    independence_map = {"api": 0.95, "csv": 0.90, "official": 0.90, "rss": 0.70, "unknown": 0.40}
    independence = independence_map.get(_infer_source_type(source_id), 0.40)

    return {
        "quality": round(quality, 2),
        "relevance": round(relevance, 2),
        "freshness": round(freshness, 2),
        "independence": round(independence, 2),
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
            "reason": _redact(node.reason[:200]),
            "evidence_ids": list(node.evidence_ids),
            "gap_before": sorted(f.value for f in node.gap_before),
            "gap_after": sorted(f.value for f in node.gap_after),
            "executions": [execution_record(ex) for ex in node.executions],
        }
        lines.append(json.dumps(entry, ensure_ascii=False))
    return "\n".join(lines)


def _redact(text: str) -> str:
    """遮罩可能的敏感資訊（API key 格式的長字串）。"""
    return re.sub(r'[A-Za-z0-9+/=_-]{20,}', '[REDACTED]', text)


def output_manifest(outcome: AnalysisOutcome) -> dict[str, Any]:
    """產出 output manifest — 包含 SHA-256、版本資訊等。"""
    report = outcome.report
    report_md = report.to_markdown() if report else ""
    evidence_json = json.dumps(
        evidence_list(report) if report else [],
        ensure_ascii=False,
    )
    log_jsonl = execution_log_jsonl(outcome)
    root = Path(".")

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
            "prompt_version": PROMPT_VERSION,
            "formula_version": FORMULA_VERSION,
            "model_version": os.environ.get("GEMINI_MODEL", "gemini-3.6-flash"),
            "model_provider": os.environ.get("MODEL_PROVIDER", "gemini"),
            "agent": "0.1.0",
            "python": sys.version.split()[0],
        },
        "source_control": {
            "commit_sha": _get_git_info("rev-parse", "HEAD", root),
            "branch": _get_git_info("rev-parse", "--abbrev-ref", "HEAD", root),
        },
        "lockfiles": {
            "uv_lock_sha256": _hash_file(root / "uv.lock"),
            "package_lock_sha256": _hash_file(root / "frontend" / "package-lock.json"),
        },
        "environment": {
            "image_digest": None,
            "os": sys.platform,
        },
    }


def _get_git_info(*args: str | Path) -> str | None:
    """取得 git 資訊。"""
    root = args[-1] if isinstance(args[-1], Path) else Path(".")
    cmd_args = [a for a in args if isinstance(a, str)]
    try:
        result = subprocess.run(
            ["git"] + cmd_args,
            capture_output=True, text=True, cwd=root, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None


def _hash_file(path: Path) -> str | None:
    """計算檔案的 SHA-256。"""
    if not path.is_file():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_submission(outcome: AnalysisOutcome, output_dir: Path) -> tuple[Path, ...]:
    run_dir = output_dir / outcome.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    report_path = run_dir / "final_report.md"
    evidence_json_path = run_dir / "evidence_list.json"
    evidence_csv_path = run_dir / "evidence_list.csv"
    log_path = run_dir / "execution_log.jsonl"
    manifest_path = run_dir / "output_manifest.json"

    if outcome.report is None:
        reason = outcome.rejection.reason if outcome.rejection else "未知原因"
        report_path.write_text(f"# 分析遭拒\n\n{reason}\n", encoding="utf-8")
        evidence_payload: list[dict[str, Any]] = []
    else:
        report_path.write_text(outcome.report.to_markdown() + "\n", encoding="utf-8")
        evidence_payload = evidence_list(outcome.report)

    evidence_json = json.dumps(evidence_payload, ensure_ascii=False, indent=2) + "\n"
    evidence_json_path.write_text(evidence_json, encoding="utf-8")

    # evidence.csv — 讓非技術人員用 Excel 檢視
    evidence_csv_path.write_text(_evidence_csv(evidence_payload), encoding="utf-8-sig")

    log_jsonl = execution_log_jsonl(outcome)
    log_path.write_text(log_jsonl + "\n", encoding="utf-8")

    manifest = output_manifest(outcome)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    return report_path, evidence_json_path, evidence_csv_path, log_path, manifest_path


def _evidence_csv(records: list[dict[str, Any]]) -> str:
    """把 evidence list 轉成 CSV 字串。"""
    import csv
    import io

    output = io.StringIO()
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow([
        "evidence_id", "facet", "source", "source_type", "fetched_at",
        "summary", "stance_hint", "sha256",
        "quality_score", "relevance_score", "freshness_score", "independence_score",
    ])
    for record in records:
        writer.writerow([
            record.get("evidence_id", ""),
            record.get("facet", ""),
            record.get("source", ""),
            record.get("source_type", ""),
            record.get("fetched_at", ""),
            record.get("summary", "")[:200],
            record.get("stance_hint", ""),
            record.get("sha256", ""),
            record.get("quality_score", ""),
            record.get("relevance_score", ""),
            record.get("freshness_score", ""),
            record.get("independence_score", ""),
        ])
    return output.getvalue()


__all__ = ["evidence_list", "execution_log", "execution_log_jsonl", "output_manifest", "write_submission"]
