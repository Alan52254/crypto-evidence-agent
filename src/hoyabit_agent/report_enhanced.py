"""增強版報告產生器 — 在標準報告基礎上加入圖表與投資者關注議題。

標準 Report.to_markdown() 產出純文字報告；本模組在其之上加入：
1. K 線圖 / 價格走勢 / RSI / 成交量的 SVG 圖表
2. 投資者常見關注議題段落（風險管理、進出場建議觀察點）
3. 結構化摘要（一眼看重點）
"""

from __future__ import annotations

from datetime import UTC, datetime

from hoyabit_agent.charts import ChartData, charts_to_markdown
from hoyabit_agent.domain import (
    AnalysisOutcome,
    ClaimRole,
    Confidence,
    Facet,
    Figure,
    FigureKind,
    InsufficientEvidence,
    Report,
    Stance,
)


def enhanced_report_markdown(outcome: AnalysisOutcome, chart_data: ChartData | None = None) -> str:
    """產出包含圖表與投資者洞察的完整報告 Markdown。"""
    report = outcome.report
    if report is None:
        return "# 分析未完成\n\n幣種不在受涵蓋範圍內或分析被拒絕。"

    sections: list[str] = []

    # ─── Executive Summary (一眼看重點) ───
    sections.append(_executive_summary(report))

    # ─── 圖表區 ───
    # 圖來自**證據自己攜帶的 figures**，而不是呼叫端額外傳進來的 chart_data。
    # 這是先前圖表從未出現在報告裡的原因：唯一的呼叫端（api_contract）
    # 不傳 chart_data，那個 if 永遠是 False。
    # 圖掛在證據上，報告只負責呈現，順帶讓每張圖都能標出它的證據識別碼。
    figures_md = _figures_section(report)
    if figures_md:
        sections.append(figures_md)
    elif chart_data and chart_data.candles:
        sections.append(charts_to_markdown(chart_data))

    # ─── 核心判斷 (按層次排列) ───
    sections.append(_layered_claims(report))

    # ─── 風險與不確定性 ───
    sections.append(_risk_section(report))

    # ─── 投資者關注議題 ───
    sections.append(_investor_insights(report))

    # ─── 證據溯源表 ───
    sections.append(_evidence_table(report))

    # ─── 方法論與限制 ───
    sections.append(_methodology(report, outcome))

    return "\n\n".join(sections)


def _executive_summary(report: Report) -> str:
    """一眼看重點的結構化摘要。"""
    stance_emoji = {"bullish": "📈", "bearish": "📉", "neutral": "➖"}
    stance_zh = {"bullish": "偏多", "bearish": "偏空", "neutral": "中性"}

    emoji = stance_emoji.get(report.stance.value, "➖")
    direction = stance_zh.get(report.stance.value, "中性")

    confidence_str = ""
    if isinstance(report.confidence, Confidence):
        pct = round(report.confidence.value * 100)
        confidence_str = f"**跨因子置信度**：{pct}%"
    elif isinstance(report.confidence, InsufficientEvidence):
        confidence_str = f"**置信度**：證據不足（{report.confidence.cause.value}）"

    facet_summary = ""
    if hasattr(report.confidence, "facet_stances"):
        stances = report.confidence.facet_stances
        facet_lines = [f"  - {f.value}：{s.value}" for f, s in stances.items()]
        facet_summary = "\n".join(facet_lines)

    return f"""# {emoji} {report.asset.value} 分析研報

> **分析題目**：{report.question}

---

## 📊 Executive Summary

| 指標 | 結果 |
|------|------|
| 研判方向 | **{direction}** {emoji} |
| {confidence_str} | |
| 分析時間窗 | {_format_window(report)} |
| 判斷條目 | {len(report.claims)} 則（已通過引用檢核） |
| 證據項數 | {len(report.evidence)} 項（來自獨立來源） |
| 被丟棄判斷 | {len(report.dropped_claims)} 則（未通過引用檢核） |

### 各面向判定
{facet_summary}"""


def _figures_section(report: Report) -> str:
    """呈現所有證據攜帶的圖表，並標明各自的證據識別碼與來源性質。

    自繪圖與外部圖分開陳列：前者可由原始數值重算，後者只是引用，
    製圖正確性不由本系統保證。把兩者混在一起會讓讀者無法判斷
    「這張圖的數字能不能核對」。
    """
    generated: list[tuple[str, Figure]] = []
    external: list[tuple[str, Figure]] = []
    for item in report.evidence:
        for figure in item.figures:
            target = generated if figure.kind is FigureKind.GENERATED else external
            target.append((item.id, figure))

    if not generated and not external:
        return ""

    lines = ["## 📈 圖表\n"]

    if generated:
        lines.append("### 本系統繪製（可由原始數值重算）\n")
        for evidence_id, figure in generated:
            lines.append(f"**{figure.caption}**　`{evidence_id}`\n")
            lines.append(f"![{figure.alt or figure.caption}]({figure.renderable_src})\n")

    if external:
        lines.append("### 外部圖表引用（製圖正確性由原始來源負責）\n")
        for evidence_id, figure in external:
            lines.append(f"**{figure.caption}**　`{evidence_id}`\n")
            lines.append(f"![{figure.alt or figure.caption}]({figure.renderable_src})\n")
            if figure.source_url:
                lines.append(f"> 原圖：{figure.source_url}\n")

    return "\n".join(lines)


def _format_window(report: Report) -> str:
    """格式化時間窗為人類可讀字串。"""
    if report.analysis_window_start and report.analysis_window_end:
        start = report.analysis_window_start.strftime("%Y-%m-%d %H:%M UTC")
        end = report.analysis_window_end.strftime("%Y-%m-%d %H:%M UTC")
        return f"{start} ~ {end}"
    if report.analysis_window_end:
        return f"截至 {report.analysis_window_end.strftime('%Y-%m-%d %H:%M UTC')}"
    return "未知"


def _confidence_breakdown(report: Report) -> str:
    """置信度分項數字 — 讓 66% 這個數字可被拆解檢視。"""
    conf = report.confidence
    if not isinstance(conf, Confidence):
        return ""
    return (
        f"\n  - 獨立性={conf.independence:.0%} | 覆蓋={conf.coverage:.0%} | "
        f"時效={conf.freshness:.0%} | 一致性={conf.agreement:.0%} | "
        f"完整性={conf.completeness:.0%} → **{conf.value:.0%}**"
    )


def _layered_claims(report: Report) -> str:
    """按 fact → inference → conclusion 分層呈現。"""
    lines = ["## 📋 核心判斷（分層結構）\n"]

    role_order = [
        (ClaimRole.FACT, "事實層 (Fact)", "直接從證據觀察到的數據"),
        (ClaimRole.INFERENCE, "推論層 (Inference)", "從多項事實交叉推導的邏輯判斷"),
        (ClaimRole.CONCLUSION, "結論層 (Conclusion)", "最終市場觀點"),
    ]

    for role, title, desc in role_order:
        claims = [c for c in report.claims if c.role == role]
        if claims:
            lines.append(f"### {title}")
            lines.append(f"*{desc}*\n")
            for claim in claims:
                citations = " ".join(f"`{eid}`" for eid in claim.evidence_ids)
                lines.append(f"- {claim.text} [{citations}]")
            lines.append("")

    return "\n".join(lines)


def _risk_section(report: Report) -> str:
    """風險、反方證據、不確定性的專區。"""
    lines = ["## ⚠️ 風險與不確定性\n"]

    counter = [c for c in report.claims if c.role == ClaimRole.COUNTER_EVIDENCE]
    risks = [c for c in report.claims if c.role == ClaimRole.RISK]
    watches = [c for c in report.claims if c.role == ClaimRole.WATCH]
    invalidations = [c for c in report.claims if c.role == ClaimRole.INVALIDATION]

    if counter:
        lines.append("### 反方證據")
        for c in counter:
            lines.append(f"- ❌ {c.text}")
        lines.append("")

    if risks:
        lines.append("### 風險因子")
        for c in risks:
            lines.append(f"- 🔴 {c.text}")
        lines.append("")

    if invalidations:
        lines.append("### 推翻條件")
        for c in invalidations:
            lines.append(f"- 🔄 {c.text}")
        lines.append("")

    if watches:
        lines.append("### 後續觀察重點")
        for c in watches:
            lines.append(f"- 👁️ {c.text}")
        lines.append("")

    if not (counter or risks or watches or invalidations):
        lines.append("*本次分析未產出風險類判斷 — 請注意這本身就是一個限制。*\n")

    return "\n".join(lines)


def _investor_insights(report: Report) -> str:
    """投資者常見關注議題 — 根據分析結果客製化。"""
    lines = ["## 💡 投資者洞察\n"]

    # DCA 建議觀察
    lines.append("### 定期定額 (DCA) 觀察")
    if report.stance == Stance.NEUTRAL:
        lines.append("- 盤整階段適合維持定期定額策略，避免追高殺低")
        lines.append("- 建議觀察區間突破方向後再調整加碼節奏")
    elif report.stance == Stance.BULLISH:
        lines.append("- 多頭趨勢中 DCA 可維持原有節奏")
        lines.append("- 若已有未實現利潤，可考慮設置移動止損保護")
    else:
        lines.append("- 空頭環境中 DCA 反而是攤平成本的好時機")
        lines.append("- 但需注意若持續下跌，應設定最大投入上限")
    lines.append("")

    # 關鍵價位
    lines.append("### 關注價位與事件")
    tech_claims = [c for c in report.claims if c.facet == Facet.TECHNICAL]
    if tech_claims:
        lines.append("根據技術面證據：")
        for tc in tech_claims[:3]:
            lines.append(f"- {tc.text}")
    else:
        lines.append("- 本次未取得足夠技術面數據以標示關鍵價位")
    lines.append("")

    # 時間框架建議
    lines.append("### 時間框架考量")
    lines.append("- **短線 (1-7天)**：關注技術面訊號與資金費率變化")
    lines.append("- **中線 (1-4週)**：關注基本面事件（升級/解鎖/監管）")
    lines.append("- **長線 (1-3月)**：關注鏈上活動趨勢與機構持倉變化")
    lines.append("")

    lines.append("> ⚠️ **免責聲明**：本報告由自動化系統依公開資料生成，僅供資訊參考，不構成投資建議。")

    return "\n".join(lines)


def _evidence_table(report: Report) -> str:
    """證據溯源表格。"""
    lines = ["## 📎 證據溯源\n"]
    lines.append("| ID | 面向 | 摘要 | 來源 | 取得時間 |")
    lines.append("|----|----|------|------|---------|")

    for ev in report.evidence:
        source_name = ev.excerpts[0].source_id.split(":")[0] if ev.excerpts else "unknown"
        fetched = ev.excerpts[0].retrieved_at.strftime("%m/%d %H:%M") if ev.excerpts else "N/A"
        summary_short = ev.summary[:50] + "..." if len(ev.summary) > 50 else ev.summary
        lines.append(f"| `{ev.id}` | {ev.facet.value} | {summary_short} | {source_name} | {fetched} |")

    return "\n".join(lines)


def _methodology(report: Report, outcome: AnalysisOutcome) -> str:
    """方法論與分析限制說明。"""
    trace_steps = len(outcome.trace.nodes)
    sources_used = {
        excerpt.source_id.split(":")[0]
        for ev in report.evidence
        for excerpt in ev.excerpts
    }

    # 已知限制**動態**產生自本回合實際計算的 report.limitations ——
    # 不再寫死「社群情緒未接入」這類宣稱,否則即時模式下系統已抓到情緒資料時
    # 報告會自打臉。無動態限制時,從 watch claims 提取限制聲明。
    if report.limitations:
        limitation_lines = "\n".join(f"- {line}" for line in report.limitations)
    else:
        # 從 watch claims 提取限制聲明（它們通常包含「缺乏」「不足」等資訊）
        from hoyabit_agent.domain import ClaimRole
        watch_claims = [c for c in report.claims if c.role == ClaimRole.WATCH]
        if watch_claims:
            # 取 watch claim 的前兩則作為限制聲明，不截斷文字
            # （截斷會產生讀起來斷掉的句子，影響報告專業度）
            limitation_lines = "\n".join(f"- {c.text}" for c in watch_claims[:2])
        else:
            limitation_lines = "- 本回合未偵測到需特別聲明的資料或推理限制"

    return f"""## 📐 方法論

- **分析框架**：ReAct (Reasoning + Acting) 循環推理
- **推理步驟**：{trace_steps} 步
- **證據來源**：{", ".join(sorted(sources_used)) or "N/A"}
- **信心度計算**：獨立性 25% + 覆蓋 25% + 時效 20% + 一致性 20% + 完整性 10%{_confidence_breakdown(report)}
- **引用檢核**：所有判斷必須掛載真實存在的證據 ID，否則系統丟棄

### 已知限制

{limitation_lines}

---

*報告生成時間：{datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")} UTC | 分析回合 ID：`{outcome.run_id}`*
"""


__all__ = ["enhanced_report_markdown"]
