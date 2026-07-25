"""把一次分析回合渲染成自帶樣式的 HTML —— 純函數，無 I/O，可測。

這是命題「看到不同 Agent 架構下，他的點對點之間為什麼結果這樣出來」的兌現：
呈現的重點不是工程師視角的 span 延遲，而是**證據如何支撐結論**。

刻意產出單一自包含 HTML（樣式內嵌、無外部資源），因此可以直接存成檔案、
用 email 寄、或塞進 iframe，不依賴任何伺服器。
"""

# ruff: noqa: E501

from __future__ import annotations

import html
import json
from collections import Counter

from hoyabit_agent.domain import (
    AnalysisOutcome,
    Confidence,
    InsufficientEvidence,
    SourceExcerpt,
    TraceNodeKind,
)
from hoyabit_agent.trace_contract import trace_node_record

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
        body = (
            '<div class="terminal-shell">'
            f'<aside class="sources-pane">{_source_rail(outcome)}</aside>'
            f'<main class="report-pane">{_header(outcome)}{_claims(outcome)}'
            f'{_evidence(outcome)}</main>'
            f'<aside class="trace-pane">{_timeline(outcome)}</aside>'
            "</div>"
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


def _source_rail(outcome: AnalysisOutcome) -> str:
    report = outcome.report
    assert report is not None
    counts = Counter(item.facet for item in report.evidence)
    facets = "".join(
        f'<li><span class="status {"on" if counts[facet] else "off"}"></span>'
        f'<span>{_e(facet.value)}</span><b>{counts[facet]}</b></li>'
        for facet in report.confidence.facet_stances
    )
    source_ids = sorted(
        {excerpt.source_id for item in report.evidence for excerpt in item.excerpts}
    )
    sources = "".join(f"<li><code>{_e(source)}</code></li>" for source in source_ids)
    return (
        '<a class=brand href="/">HOYA BIT <span>INTELLIGENCE</span></a>'
        '<p class=kicker>Evidence workspace</p>'
        '<nav aria-label="本回合資料覆蓋"><h2>市場因子覆蓋</h2>'
        f'<ul class=coverage>{facets}</ul></nav>'
        '<section><h2>本回合資料源</h2>'
        f'<ul class=source-list>{sources}</ul></section>'
        '<p class=rail-note>燈號表示本回合是否取得證據，不代表外部服務永久在線。</p>'
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
        '<section class="card market-summary"><p class=eyebrow>MARKET INTELLIGENCE</p>'
        f'<h1>{_e(report.asset.value)} 分析報告 · {_e(report.question)}</h1>'
        f'<p class=decision><b>市場方向</b> <span class="stance {_e(report.stance.value)}">'
        f'{_e(report.stance.value)}</span> <b>跨因子一致度</b> {conf}</p>'
        '<p class=cutoff>歷史資料截止於 2026-05-31 UTC；外部證據依 fetched_at 為準。</p>'
        f"<table class=facets><tr><th>金融因子</th><th>訊號方向</th></tr>{rows}</table>"
        f"<p class=meta>RUN {_e(outcome.run_id)}</p></section>"
    )


def _trace_label(kind: TraceNodeKind) -> str:
    if kind is TraceNodeKind.PLAN or kind is TraceNodeKind.SYNTHESISE:
        return "THOUGHT"
    if kind is TraceNodeKind.GATHER or kind is TraceNodeKind.REPORT:
        return "OBSERVATION"
    if kind in {
        TraceNodeKind.SOURCE_UNAVAILABLE,
        TraceNodeKind.BUDGET_EXHAUSTED,
        TraceNodeKind.CLAIM_DROPPED,
    }:
        return "WARNING"
    if kind is TraceNodeKind.GAP_CHECK:
        return "THOUGHT · GAP CHECK"
    return str(kind.value).upper()

def _timeline(outcome: AnalysisOutcome) -> str:
    budget = max((node.elapsed_seconds for node in outcome.trace.nodes), default=0.0) or 1.0
    items = []
    for node in outcome.trace.nodes:
        colour = _KIND_COLOUR.get(node.kind, "#6b7280")
        width = min(100.0, node.elapsed_seconds / budget * 100.0)
        detail = "".join(
            f'<div class=arg><span class=tool>ACTION · {_e(item.tool)} [{_e(item.asset.value)}]</span>'
            f'<code>{_e(json.dumps(dict(item.arguments), ensure_ascii=False, sort_keys=True))}</code>'
            f'<div class=produced>OBSERVATION · {_e(item.status.value)} · '
            f'{_e(item.observation)}</div></div>'
            for item in node.executions
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
            f'<div class=kind style="color:{colour}">{_e(_trace_label(node.kind))}'
            f'<span class=t>{node.elapsed_seconds:.2f}s</span></div>'
            f"<div class=reason>{_e(node.reason)}</div>"
            f"{detail}{produced}{gap}"
            f'<div class=track><div class=bar style="width:{width:.1f}%;'
            f'background:{colour}"></div></div>'
            f"</div></li>"
        )
    nodes = "".join(items)
    elapsed = max((node.elapsed_seconds for node in outcome.trace.nodes), default=0.0)
    return (
        '<section class="trace-card"><header><div><p class=eyebrow>AGENT RUNTIME</p>'
        '<h2>決策軌跡</h2></div>'
        f'<div class=timer>{elapsed:05.1f}s <span>/ 900s</span></div></header>'
        '<div class=trace-legend><span>THOUGHT</span><span>ACTION</span>'
        '<span>OBSERVATION</span><span>WARNING</span></div>'
        f'<ol class=timeline>{nodes}</ol></section>'
    )


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
    """Serialize the same lossless contract consumed by SSE and Next.js."""
    return json.dumps(
        {
            "run_id": outcome.run_id,
            "nodes": [
                trace_node_record(outcome.run_id, node) for node in outcome.trace.nodes
            ],
        },
        ensure_ascii=False,
        indent=2,
    )

_PAGE = """<!doctype html>
<html lang=zh-Hant><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>{title} · HOYA BIT Intelligence</title>
<style>
:root{{color-scheme:dark;--bg:#070b12;--panel:#0c121d;--raised:#111a28;--line:#223047;
--text:#e7edf6;--muted:#94a3b8;--gold:#f6b94a;--blue:#5aa7ff;--green:#48c78e;
--purple:#aa8cff;--warning:#ff9f43;--danger:#ff667a}}
*{{box-sizing:border-box}} html{{background:var(--bg)}}
body{{margin:0;background:var(--bg);color:var(--text);font:14px/1.55 Inter,ui-sans-serif,
system-ui,"Noto Sans TC",sans-serif}} a{{color:inherit}} button,a,summary{{touch-action:manipulation}}
#app{{min-height:100dvh}} .terminal-shell{{display:grid;grid-template-columns:minmax(210px,16vw)
minmax(460px,1fr) minmax(340px,30vw);min-height:100dvh}}
.sources-pane,.trace-pane{{background:var(--panel);position:sticky;top:0;height:100dvh;overflow:auto}}
.sources-pane{{border-right:1px solid var(--line);padding:24px 16px}}
.trace-pane{{border-left:1px solid var(--line);padding:20px}}
.report-pane{{min-width:0;padding:28px clamp(20px,3vw,48px);overflow:auto}}
.brand{{display:block;color:var(--gold);font:700 15px ui-monospace,monospace;letter-spacing:.12em;
text-decoration:none;padding:8px 6px}} .brand span{{display:block;color:var(--muted);font-size:9px}}
.kicker,.eyebrow{{color:var(--muted);font:600 10px ui-monospace,monospace;letter-spacing:.14em;
text-transform:uppercase}} .sources-pane h2{{font-size:11px;color:var(--muted);margin:28px 6px 10px;
text-transform:uppercase;letter-spacing:.08em}}
ul.coverage,ul.source-list{{list-style:none;padding:0;margin:0}} .coverage li{{display:grid;
grid-template-columns:10px 1fr auto;align-items:center;gap:9px;min-height:38px;padding:0 8px;
border-bottom:1px solid rgba(148,163,184,.08);text-transform:capitalize}}
.coverage b{{font:600 12px ui-monospace,monospace}} .status{{width:7px;height:7px;border-radius:50%;
background:#465269}} .status.on{{background:var(--green);box-shadow:0 0 0 3px rgba(72,199,142,.1)}}
.source-list li{{padding:7px 8px;color:#b8c4d6;overflow-wrap:anywhere}} .source-list code{{font-size:11px}}
.rail-note{{margin:24px 6px;color:var(--muted);font-size:11px}}
.report-head{{display:flex;justify-content:space-between;gap:24px;border-bottom:1px solid var(--line);
padding-bottom:22px}} h1{{font-size:clamp(21px,2.3vw,32px);line-height:1.25;margin:7px 0 9px;
max-width:760px}} .verdict{{min-width:130px;text-align:right}} .verdict strong{{display:block;
font:700 30px ui-monospace,monospace;margin-top:8px}} .verdict small,.cutoff{{color:var(--muted)}}
.stance,.signal{{display:inline-flex;border:1px solid currentColor;border-radius:999px;padding:3px 9px;
font:700 10px ui-monospace,monospace;text-transform:uppercase}}
.bullish{{color:var(--green)}} .bearish{{color:var(--danger)}} .neutral{{color:var(--warning)}}
.factor-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:18px 0}}
.factor{{background:var(--raised);border:1px solid var(--line);border-radius:8px;padding:12px;min-width:0}}
.factor>span{{display:block;color:var(--muted);font:600 10px ui-monospace,monospace;text-transform:uppercase}}
.factor .signal{{margin:10px 0 6px}} .factor small{{display:block;color:var(--muted)}}
.run-meta{{display:grid;grid-template-columns:1fr auto;gap:6px;color:var(--muted);
font:10px ui-monospace,monospace}} progress{{grid-column:1/-1;width:100%;height:3px;accent-color:var(--gold)}}
.market-summary{{border-bottom:1px solid var(--line);padding-bottom:20px}}
.decision{{display:flex;align-items:center;gap:10px;flex-wrap:wrap}}
table.facets{{width:100%;border-collapse:collapse;margin:16px 0}}
table.facets th,table.facets td{{padding:9px;border-bottom:1px solid var(--line);text-align:left;text-transform:capitalize}}
table.facets th{{color:var(--muted);font:600 10px ui-monospace,monospace;text-transform:uppercase}}
.card{{margin:28px 0}} .card h2,.trace-card h2{{font-size:16px;margin:0}}
ul.claims{{list-style:none;padding:0;margin:12px 0}} .claim{{background:var(--raised);border:1px solid var(--line);
border-left:3px solid var(--gold);border-radius:7px;padding:14px 16px;margin:9px 0;line-height:1.7}}
a.cite{{display:inline-flex;background:#17243a;color:#b8d9ff;border:1px solid #29466c;border-radius:4px;
padding:1px 6px;margin:2px;font:10px ui-monospace,monospace;text-decoration:none}}
a.cite:hover,a.cite:focus-visible{{background:#24466f;outline:2px solid var(--blue);outline-offset:2px}}
.note,.meta{{color:var(--muted);font-size:11px}} .dropped{{color:var(--muted);padding:8px}}
.dropped .why{{color:var(--danger);font-size:11px;margin-left:8px}}
details.evi{{border:1px solid var(--line);border-radius:7px;margin:7px 0;background:var(--panel)}}
details.evi summary{{cursor:pointer;padding:11px 13px;min-height:44px}} details.evi[open]{{border-color:#385172}}
.facet{{color:var(--purple);font-size:10px;text-transform:uppercase;margin:0 5px}} .hint{{color:var(--muted)}}
.excerpt{{padding:12px 14px;border-top:1px solid var(--line);color:#cbd5e1}} .excerpt a{{display:block;
color:var(--blue);font-size:11px;overflow-wrap:anywhere;margin-top:7px}}
.trace-card>header{{display:flex;justify-content:space-between;align-items:start;position:sticky;top:-20px;
background:var(--panel);padding:20px 0 12px;z-index:2}} .timer{{font:700 15px ui-monospace,monospace;
color:var(--gold);font-variant-numeric:tabular-nums}} .timer span{{color:var(--muted);font-size:10px}}
.trace-legend{{display:flex;flex-wrap:wrap;gap:5px;margin:8px 0 16px}} .trace-legend span{{border:1px solid var(--line);
border-radius:3px;padding:2px 5px;color:var(--muted);font:9px ui-monospace,monospace}}
ol.timeline{{list-style:none;margin:0;padding:0}} .node{{display:flex;gap:10px;padding:12px 0;border-top:1px solid var(--line)}}
.node .seq{{font:10px ui-monospace,monospace;color:var(--muted);min-width:22px}} .node .body{{min-width:0;flex:1}}
.kind{{font:700 10px ui-monospace,monospace;letter-spacing:.08em}} .kind .t{{float:right;color:var(--muted);
font-weight:400;font-variant-numeric:tabular-nums}} .reason{{font-size:12px;margin:5px 0;white-space:pre-wrap}}
.arg{{border-left:2px solid var(--blue);padding:5px 8px;margin:5px 0;background:#0a1625}}
.arg .tool{{display:block;color:var(--blue);font:600 11px ui-monospace,monospace}} .arg code,.produced,.gap{{font-size:10px;
color:var(--muted);overflow-wrap:anywhere}} .produced{{border-left:2px solid var(--green);padding-left:8px}}
.gap{{border-left:2px solid var(--purple);padding-left:8px;margin-top:5px}} .track{{height:2px;background:#1a2638;
margin-top:8px;overflow:hidden}} .track .bar{{height:100%}} :target{{outline:2px solid var(--gold);outline-offset:3px}}
.reject{{max-width:640px;margin:60px auto;padding:24px;border:1px solid var(--danger)}}
@media(max-width:1100px){{.terminal-shell{{grid-template-columns:190px 1fr}}.trace-pane{{grid-column:1/-1;
position:static;height:auto;border-left:0;border-top:1px solid var(--line)}}}}
@media(max-width:720px){{.terminal-shell{{display:block}}.sources-pane{{position:static;height:auto;border-right:0;
border-bottom:1px solid var(--line)}}.source-list{{display:none}}.sources-pane section h2{{display:none}}
.report-pane{{padding:20px 14px}}.report-head{{display:block}}.verdict{{text-align:left;margin-top:14px}}
.factor-grid{{grid-template-columns:repeat(2,1fr)}}.trace-pane{{padding:14px}}}}
@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition:none!important}}}}
</style></head><body><div id=app>{body}</div></body></html>"""


__all__ = ["render_outcome", "trace_json"]
