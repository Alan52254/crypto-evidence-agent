from __future__ import annotations

import json
from pathlib import Path

from hoyabit_agent.artifacts import write_submission
from hoyabit_agent.domain import AnalysisRequest, DraftClaim, Facet
from hoyabit_agent.run import analyse
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence


async def test_question_reaches_planner_and_final_report() -> None:
    item = evidence("E-TECH", Facet.TECHNICAL, 0.3)
    model = ScriptedModel(
        plans=[("market", "回答現場題目")],
        claims=[DraftClaim("價格偏強", ("E-TECH",), Facet.TECHNICAL)],
    )
    outcome = await analyse(
        AnalysisRequest("BTC", question="短期是否維持盤整？"),
        [StaticSource([item], name="market")],
        model,
    )
    assert model.seen_contexts[0].question == "短期是否維持盤整？"
    assert outcome.report is not None
    assert outcome.report.question == "短期是否維持盤整？"
    assert "短期是否維持盤整？" in outcome.report.to_markdown()


async def test_submission_contains_traceable_claim_mapping(tmp_path: Path) -> None:
    item = evidence("E-1", Facet.FUNDAMENTAL, 0.4, text="官方公告內容")
    outcome = await analyse(
        AnalysisRequest("BTC", question="事件是否構成利多？"),
        [StaticSource([item], name="official")],
        ScriptedModel(
            plans=[("official", "查官方來源")],
            claims=[DraftClaim("事件偏正面", ("E-1",), Facet.FUNDAMENTAL)],
        ),
    )
    paths = write_submission(outcome, tmp_path)
    assert {path.name for path in paths} == {
        "final_report.md",
        "evidence_list.json",
        "execution_log.jsonl",
        "output_manifest.json",
    }
    records = json.loads(paths[1].read_text(encoding="utf-8"))
    assert records[0]["related_claim"] == ["事件偏正面"]
    assert records[0]["sources"][0]["fetched_at"]
    # execution_log.jsonl — 每行一個 JSON event
    log_lines = [json.loads(line) for line in paths[2].read_text(encoding="utf-8").strip().split("\n") if line.strip()]
    assert any(entry.get("executions") for entry in log_lines)
