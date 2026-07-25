"""Demo CLI —— 跑一次分析回合並印出報告與推論軌跡。

預設跑離線的假證據源（快、可重現、適合展示流程）；
加上 `--live` 就改用 Gemini 與競賽 OHLCV 資料集 ——
**分析回合本身一行都不用改**，那正是接縫存在的意義。
"""

from __future__ import annotations

import argparse
import contextlib
import sys
from collections.abc import AsyncIterator
from pathlib import Path

import httpx

from hoyabit_agent import AnalysisRequest, analyse
from hoyabit_agent.artifacts import write_submission
from hoyabit_agent.config import run_async
from hoyabit_agent.domain import (
    AnalysisOutcome,
    DraftClaim,
    Facet,
    Trace,
)
from hoyabit_agent.models.gemini import API_KEY_ENV, GeminiProvider
from hoyabit_agent.seams import ModelProvider, Sources
from hoyabit_agent.storage.postgres import (
    PostgresAnalysisStore,
    database_url,
    reachable,
)
from hoyabit_agent.testing import ScriptedModel, StaticSource, evidence


def _demo_sources() -> Sources:
    return [
        StaticSource(
            [
                evidence("EV-MKT-01", Facet.TECHNICAL, +0.7, text="日線站上 60MA，量能放大 38%"),
                evidence("EV-MKT-02", Facet.POSITIONING, +0.4, text="資金費率轉正但未過熱"),
            ],
            name="market",
        ),
        StaticSource(
            [
                evidence(
                    "EV-NEWS-01",
                    Facet.FUNDAMENTAL,
                    +0.5,
                    event_key="etf-inflow",
                    text="現貨 ETF 連續五日淨流入",
                ),
                evidence(
                    "EV-NEWS-02",
                    Facet.SENTIMENT,
                    -0.6,
                    text="社群對短線過熱表達疑慮",
                ),
                # 同一事件的轉載 —— 歸併後只會算一個證據
                evidence(
                    "EV-NEWS-03",
                    Facet.FUNDAMENTAL,
                    +0.5,
                    event_key="etf-inflow",
                    text="另一家媒體報導同一則 ETF 淨流入消息",
                ),
            ],
            name="news",
        ),
        StaticSource([], name="flaky", raises=RuntimeError("upstream 503")),
    ]


def _demo_model() -> ModelProvider:
    return ScriptedModel(
        plans=[
            ("market,flaky", "四個證據面全缺，先抓市場數據，順便試試備援源"),
            ("news", "技術面與籌碼面已補上，基本面與情緒面仍缺，改抓新聞"),
        ],
        arguments={
            "market": {"interval": "1d", "limit": 200},
            "news": {"query": "BTC ETF 流入", "hours": 24},
        },
        claims=[
            DraftClaim("日線站上 60MA 且量能同步放大", ("EV-MKT-01",), Facet.TECHNICAL),
            DraftClaim("資金費率轉正，但尚未進入過熱區間", ("EV-MKT-02",), Facet.POSITIONING),
            DraftClaim("現貨 ETF 連續淨流入", ("EV-NEWS-01",), Facet.FUNDAMENTAL),
            DraftClaim("社群對短線漲勢轉趨保留", ("EV-NEWS-02",), Facet.SENTIMENT),
            DraftClaim("因此後市必然續漲", (), Facet.TECHNICAL),  # 無證據 —— 會被丟棄
        ],
    )


def _pick_model(client: httpx.AsyncClient) -> tuple[ModelProvider | None, str]:
    """Gemini 是唯一正式推理 adapter。"""
    gemini = GeminiProvider.from_environment(client)
    if gemini is not None:
        return gemini, f"Gemini（{API_KEY_ENV}）"
    return None, ""


@contextlib.asynccontextmanager
async def _live_stack() -> AsyncIterator[tuple[Sources, ModelProvider, str]]:
    """只暴露 Gemini 與競賽 OHLCV 資料集；缺 key 時明確失敗。"""
    from hoyabit_agent.config import load_dotenv
    from hoyabit_agent.ingest.runtime import build_competition_sources

    load_dotenv()
    async with httpx.AsyncClient(timeout=90.0) as client:
        model, description = _pick_model(client)
        if model is None:
            raise RuntimeError("缺少 GEMINI_API_KEY，正式分析不提供非 Gemini fallback")
        sources = await build_competition_sources(client, model)
        yield sources, model, description


def _render_trace(trace: Trace) -> str:
    lines = [f"# 推論軌跡  run={trace.run_id}", ""]
    for node in trace.nodes:
        lines.append(f"[{node.seq:02d}] {node.elapsed_seconds:6.2f}s  {node.kind.value}")
        lines.append(f"      理由：{node.reason}")
        for execution in node.executions:
            lines.append(
                f"      調用 {execution.tool}[{execution.asset.value}]"
                f"({dict(execution.arguments)}) → {execution.status.value}: "
                f"{execution.observation}"
            )
        if node.evidence_ids:
            lines.append(f"      產出證據：{', '.join(node.evidence_ids)}")
        if node.gap_before != node.gap_after:
            before = "、".join(sorted(f.value for f in node.gap_before)) or "無"
            after = "、".join(sorted(f.value for f in node.gap_after)) or "無"
            lines.append(f"      缺口：{before}  →  {after}")
        lines.append("")
    return "\n".join(lines)


def _render_evidence(outcome: AnalysisOutcome) -> str:
    assert outcome.report is not None
    lines = ["# 證據與來源片段", ""]
    for item in outcome.report.evidence:
        lines.append(f"- [{item.id}] {item.facet.value}  傾向 {item.stance_hint:+.2f}")
        for excerpt in item.excerpts:
            lines.append(f"    「{excerpt.text}」")
            lines.append(f"    出處 {excerpt.url}  擷取於 {excerpt.retrieved_at:%Y-%m-%d %H:%M}")
    return "\n".join(lines)


def _write_html(outcome: AnalysisOutcome, path: str) -> None:
    """把這次回合渲染成自包含 HTML 存檔 —— 可直接用瀏覽器打開，不需要 server。"""
    from pathlib import Path

    from hoyabit_agent.viz.trace_html import render_outcome

    Path(path).write_text(render_outcome(outcome), encoding="utf-8")
    print(f"（已輸出軌跡 HTML：{path}）")


async def _persist(outcome: AnalysisOutcome) -> None:
    """把回合寫進資料庫。

    連不到就印一行提示繼續 —— 沒有資料庫不該讓一次成功的分析變成失敗。
    """
    if not await reachable():
        print(f"（連不到 {database_url()}，這次分析沒有存檔）")
        return
    await PostgresAnalysisStore.from_environment().save(outcome)
    print(f"（已存檔：run_id={outcome.run_id}）")


async def _run(
    asset: str,
    *,
    question: str = "請分析當前市場狀況",
    live: bool,
    save: bool,
    html_path: str | None,
    output_dir: Path | None = None,
) -> int:
    request = AnalysisRequest(asset=asset, question=question)
    if live:
        try:
            async with _live_stack() as (sources, model, description):
                print(f"（推理層：{description}）")
                print()
                outcome = await analyse(request, sources, model)
        except RuntimeError as error:
            print(str(error))
            return 2
    else:
        outcome = await analyse(request, _demo_sources(), _demo_model())
    if output_dir is not None:
        paths = write_submission(outcome, output_dir)
        print(f"（提交物：{paths[0].parent}）")

    if outcome.rejection is not None:
        print(f"已拒絕：{outcome.rejection.reason}")
        print()
        print(_render_trace(outcome.trace))
        if save:
            await _persist(outcome)
        if html_path:
            _write_html(outcome, html_path)
        return 1

    assert outcome.report is not None
    print(outcome.report.to_markdown())
    print()
    print(_render_evidence(outcome))
    print()
    print(_render_trace(outcome.trace))

    # 信心度不在這裡重畫 —— `to_markdown` 已經渲染過，
    # 第二份渲染只會慢慢跟第一份漂開（而且上一版就漏掉了兩種
    # 「算不出來」的原因必須分開表達這件事）。
    dropped = outcome.report.dropped_claims
    print(f"引用檢核：保留 {len(outcome.report.claims)} 則，丟棄 {len(dropped)} 則")
    for draft in dropped:
        print(f"  丟棄 ── {draft.text}（未掛載有效證據）")

    if save:
        print()
        await _persist(outcome)
    if html_path:
        _write_html(outcome, html_path)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="跑一次加密貨幣分析回合（demo）")
    parser.add_argument("asset", help="受涵蓋幣種：BTC / ETH / SOL / BNB / XRP")
    parser.add_argument(
        "-q",
        "--question",
        default="請分析當前市場狀況",
        help="競賽現場公布的完整分析題目",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="改用 Gemini 與競賽 OHLCV 資料集",
    )
    parser.add_argument(
        "--save",
        action="store_true",
        help="把這次回合存進 Postgres（連不到就略過，不會讓分析失敗）",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=None,
        help="提交物根目錄；live 模式預設 submissions/",
    )
    parser.add_argument(
        "--html",
        metavar="PATH",
        help="把推論軌跡輸出成自包含 HTML 檔（可直接用瀏覽器開）",
    )
    args = parser.parse_args(argv)
    return int(
        run_async(
            _run(
                args.asset,
                question=args.question,
                live=args.live,
                save=args.save,
                html_path=args.html,
                output_dir=args.output_dir or (Path("submissions") if args.live else None),
            )
        )
    )


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
