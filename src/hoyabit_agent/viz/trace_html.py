"""把一次分析回合渲染成自帶樣式的 HTML —— 純函數，無 I/O，可測。

這是命題「看到不同 Agent 架構下，他的點對點之間為什麼結果這樣出來」的兌現：
呈現的重點不是工程師視角的 span 延遲，而是**證據如何支撐結論**。

刻意產出單一自包含 HTML（樣式內嵌、無外部資源），因此可以直接存成檔案、
用 email 寄、或塞進 iframe，不依賴任何伺服器。
"""

from __future__ import annotations

import html
import json

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Confidence,
    InsufficientEvidence,
    SourceExcerpt,
    TraceNodeKind,
)

# 每種節點一個顏色，讓時間軸一眼看出「規劃 vs 蒐集 vs 組裝」的節奏。
_KIND_COLOUR = {
    TraceNodeKind.ASSET_GATE: "#6b7280",
    TraceNodeKind.PLAN: "#2563eb",
    TraceNodeKind.GATHER: "#16a34a",
    TraceNodeKind.SOURCE_UNAVAILABLE: "#d97706",
    TraceNodeKind.GAP_CHECK: "#7c3aed",
    TraceNodeKind.BUDGET_EXHAUSTED: "#dc2626",
    TraceNodeKind.SYNTHESISE: "#0891b2",
    TraceNodeKind.CLAIM_DROPPED: "#dc2626",
    TraceNodeKind.REPORT: "#111827",
}


def _e(text: object) -> str:
    return html.escape(str(text))


def render_outcome(outcome: AnalysisOutcome) -> str:
    """渲染一次回合的完整 HTML。"""
    if outcome.rejection is not None:
        body = _rejection(outcome)
    else:
        body = "\n".join(
            [_header(outcome), _timeline(outcome), _evidence(outcome), _claims(outcome)]
        )
    return _PAGE.format(title=_e(outcome.run_id), body=body)


def _rejection(outcome: AnalysisOutcome) -> str:
    assert outcome.rejection is not None
    return (
        f'<section class="card reject"><h2>已拒絕</h2>'
        f"<p>{_e(outcome.rejection.reason)}</p>"
        f"<p class=meta>回合 {_e(outcome.run_id)}</p></section>"
        + _timeline(outcome)
    )


def _header(outcome: AnalysisOutcome) -> str:
    report = outcome.report
    assert report is not None
    confidence = report.confidence
    if isinstance(confidence, Confidence):
        conf = f"{confidence.value:.0%}（證據面之間的一致程度）"
    elif isinstance(confidence, InsufficientEvidence):
        conf = "無法計算（" + _e(confidence.cause.value) + "）"
    else:
        conf = "—"

    rows = "".join(
        f"<tr><td>{_e(facet.value)}</td><td>{_e(stance.value)}</td></tr>"
        for facet, stance in sorted(
            report.confidence.facet_stances.items(), key=lambda kv: kv[0].value
        )
    )
    return (
        f'<section class="card"><h1>{_e(report.asset.value)} 分析報告</h1>'
        f"<p><b>方向</b>：{_e(report.stance.value)}　<b>信心度</b>：{conf}</p>"
        f"<table class=facets><tr><th>證據面</th><th>傾向</th></tr>{rows}</table>"
        f"<p class=meta>回合 {_e(outcome.run_id)}</p></section>"
    )


def _timeline(outcome: AnalysisOutcome) -> str:
    budget = max((node.elapsed_seconds for node in outcome.trace.nodes), default=0.0) or 1.0
    items = []
    for node in outcome.trace.nodes:
        colour = _KIND_COLOUR.get(node.kind, "#6b7280")
        width = min(100.0, node.elapsed_seconds / budget * 100.0)
        detail = "".join(
            f'<div class=arg><span class=tool>{_e(tool)}</span>'
            f"<code>{_e(args)}</code></div>"
            for tool, args in sorted(node.detail.items())
        )
        produced = (
            f'<div class=produced>產出證據：{_e("、".join(node.evidence_ids))}</div>'
            if node.evidence_ids
            else ""
        )
        gap = ""
        if node.gap_before != node.gap_after:
            before = "、".join(sorted(f.value for f in node.gap_before)) or "無"
            after = "、".join(sorted(f.value for f in node.gap_after)) or "無"
            gap = f'<div class=gap>缺口：{_e(before)} → {_e(after)}</div>'
        items.append(
            f'<li class=node style="--c:{colour}">'
            f"<div class=seq>{node.seq:02d}</div>"
            f"<div class=body>"
            f'<div class=kind style="color:{colour}">{_e(node.kind.value)}'
            f'<span class=t>{node.elapsed_seconds:.2f}s</span></div>'
            f"<div class=reason>{_e(node.reason)}</div>"
            f"{detail}{produced}{gap}"
            f'<div class=track><div class=bar style="width:{width:.1f}%;'
            f'background:{colour}"></div></div>'
            f"</div></li>"
        )
    nodes = "".join(items)
    return f'<section class="card"><h2>推論軌跡</h2><ol class=timeline>{nodes}</ol></section>'


def _excerpt_html(ex: SourceExcerpt) -> str:
    when = ex.retrieved_at.strftime("%Y-%m-%d %H:%M")
    return (
        f'<div class=excerpt>「{_e(ex.text)}」'
        f'<a href="{_e(ex.url)}" target=_blank rel=noopener>{_e(ex.url)}</a>'
        f"<span class=meta>擷取於 {_e(when)}</span></div>"
    )


def _evidence(outcome: AnalysisOutcome) -> str:
    report = outcome.report
    assert report is not None
    if not report.evidence:
        return ""
    items = []
    for item in report.evidence:
        excerpts = "".join(_excerpt_html(ex) for ex in item.excerpts)
        items.append(
            f'<details class=evi id="evi-{_e(item.id)}">'
            f"<summary><code>{_e(item.id)}</code> "
            f"<span class=facet>{_e(item.facet.value)}</span> "
            f'<span class=hint>傾向 {item.stance_hint:+.2f}</span> '
            f"{_e(item.summary)}</summary>{excerpts}</details>"
        )
    return f'<section class="card"><h2>證據與來源片段</h2>{"".join(items)}</section>'


def _claims(outcome: AnalysisOutcome) -> str:
    report = outcome.report
    assert report is not None
    kept = "".join(
        f"<li class=claim>{_e(claim.text)} "
        + "".join(
            f'<a class=cite href="#evi-{_e(eid)}">{_e(eid)}</a>'
            for eid in claim.evidence_ids
        )
        + "</li>"
        for claim in report.claims
    )
    dropped = "".join(
        f"<li class=dropped><s>{_e(draft.text)}</s>"
        "<span class=why>未掛載有效證據，已被引用檢核丟棄</span></li>"
        for draft in report.dropped_claims
    )
    dropped_block = (
        f"<h3>已丟棄（未通過引用檢核）</h3><ul class=claims>{dropped}</ul>" if dropped else ""
    )
    return (
        f'<section class="card"><h2>判斷</h2>'
        f"<p class=note>點證據標記可跳到對應的原始片段。</p>"
        f"<ul class=claims>{kept}</ul>{dropped_block}</section>"
    )


def trace_json(outcome: AnalysisOutcome) -> str:
    """把軌跡輸出成 JSON —— 軌跡檔本身是交付物，可供其他工具消費。"""
    return json.dumps(
        {
            "run_id": outcome.run_id,
            "nodes": [
                {
                    "seq": node.seq,
                    "kind": node.kind.value,
                    "reason": node.reason,
                    "evidence_ids": list(node.evidence_ids),
                    "gap_before": sorted(f.value for f in node.gap_before),
                    "gap_after": sorted(f.value for f in node.gap_after),
                    "elapsed_seconds": node.elapsed_seconds,
                    "detail": dict(node.detail),
                }
                for node in outcome.trace.nodes
            ],
        },
        ensure_ascii=False,
        indent=2,
    )


_PAGE = """<!doctype html>
<html lang=zh-Hant><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title} · 推論軌跡</title>
<style>
:root{{color-scheme:light dark}}
body{{font:15px/1.6 system-ui,"Noto Sans TC",sans-serif;margin:0;background:#f3f4f6;color:#111827}}
main{{max-width:820px;margin:0 auto;padding:24px 16px}}
.card{{background:#fff;border-radius:12px;padding:20px 24px;margin:0 0 20px;
  box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card.reject{{border-left:4px solid #dc2626}}
h1{{font-size:22px;margin:0 0 8px}} h2{{font-size:17px;margin:0 0 12px}}
h3{{font-size:15px;margin:16px 0 8px;color:#6b7280}}
.meta{{color:#9ca3af;font-size:12px}}
table.facets{{border-collapse:collapse;margin:8px 0}}
table.facets td,table.facets th{{border:1px solid #e5e7eb;padding:4px 12px;text-align:left}}
ol.timeline{{list-style:none;margin:0;padding:0}}
.node{{display:flex;gap:12px;padding:10px 0;border-top:1px solid #f3f4f6}}
.node .seq{{font:12px monospace;color:#9ca3af;min-width:24px}}
.node .body{{flex:1}}
.kind{{font-weight:600;font-size:13px}} .kind .t{{color:#9ca3af;font-weight:400;margin-left:8px}}
.reason{{margin:2px 0}}
.arg{{font-size:13px;margin:2px 0}} .arg .tool{{color:#2563eb;margin-right:6px}}
.arg code,.produced,.gap{{font-size:12px;color:#6b7280}}
.produced,.gap{{margin:2px 0}}
.track{{height:4px;background:#f3f4f6;border-radius:2px;margin-top:6px;overflow:hidden}}
.track .bar{{height:100%}}
details.evi{{border:1px solid #e5e7eb;border-radius:8px;padding:8px 12px;margin:6px 0}}
details.evi[open]{{background:#fafafa}}
details.evi summary{{cursor:pointer}}
.facet{{background:#eef2ff;color:#3730a3;border-radius:4px;padding:0 6px;font-size:12px}}
.hint{{color:#6b7280;font-size:12px}}
.excerpt{{margin:8px 0 0;padding-left:12px;border-left:2px solid #e5e7eb}}
.excerpt a{{display:block;font-size:12px;color:#2563eb;word-break:break-all}}
ul.claims{{margin:0;padding-left:18px}}
.claim{{margin:6px 0}}
a.cite{{display:inline-block;background:#111827;color:#fff;border-radius:4px;
  padding:0 6px;margin-left:4px;font:12px monospace;text-decoration:none}}
a.cite:hover{{background:#2563eb}}
.dropped .why{{color:#dc2626;font-size:12px;margin-left:8px}}
.note{{color:#9ca3af;font-size:12px;margin:0 0 8px}}
:target{{outline:2px solid #2563eb;outline-offset:2px}}
</style></head><body><main>{body}</main></body></html>"""


__all__ = ["render_outcome", "trace_json"]
