"""Demo CLI 的煙霧測試 —— 確保可展示的切片不會靜默壞掉。"""

from __future__ import annotations

import pytest

from hoyabit_agent.cli import main


def test_a_covered_asset_prints_a_report_a_trace_and_the_disclaimer(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["BTC"]) == 0
    out = capsys.readouterr().out

    assert "# BTC 分析報告" in out
    assert "**方向**" in out
    assert "**信心度**" in out
    assert "# 推論軌跡" in out
    assert "不是投資建議" in out


def test_the_trace_shows_the_gap_driving_the_next_action(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """這正是命題所說的「點對點之間為什麼」。"""
    assert main(["BTC"]) == 0
    out = capsys.readouterr().out

    assert "缺口：" in out
    assert "→" in out
    assert out.count("plan") >= 2  # 模型至少改變過一次策略


def test_an_unavailable_source_is_reported_without_killing_the_run(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["BTC"]) == 0
    out = capsys.readouterr().out

    assert "暫時不可用" in out
    assert "## 判斷" in out  # 仍然產出了判斷


def test_a_claim_without_evidence_is_dropped_and_reported(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["BTC"]) == 0
    out = capsys.readouterr().out

    assert "因此後市必然續漲" not in out.split("# 證據與來源片段")[0]
    assert "丟棄 1 則" in out


def test_transmitted_coverage_is_merged_but_both_excerpts_survive(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["BTC"]) == 0
    out = capsys.readouterr().out

    # 兩則轉載歸併成一項證據，但兩份來源片段都還在
    assert "另一家媒體報導同一則 ETF 淨流入消息" in out
    assert "EV-NEWS-03]" not in out  # 不以獨立證據的身分出現


def test_an_uncovered_asset_exits_non_zero_with_only_a_gate_node(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["DOGE"]) == 1
    out = capsys.readouterr().out

    assert "已拒絕" in out
    assert "asset_gate" in out
    assert "gather" not in out
