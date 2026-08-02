"""PDF 報告生成器 — 將結構化分析成果渲染為 4 份排版精美的 PDF。

產出：
1. generate_report_pdf() — 分析報告 PDF
2. generate_evidence_pdf() — 證據清單 PDF（表格化）
3. generate_trace_pdf() — 執行紀錄 PDF（等寬字型）
4. generate_config_pdf() — 系統配置 PDF

字體策略：嘗試載入系統中文字體（微軟正黑體 / Noto Sans CJK），
找不到時 fallback 到 reportlab 內建字體。
"""

from __future__ import annotations

import io
import json
import logging
import os
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from hoyabit_agent.domain import AnalysisOutcome, Figure

logger = logging.getLogger(__name__)

# ─── 中文字體註冊 ───
_FONT_REGISTERED = False
_CJK_FONT_NAME = "AlphaSonarCJK"
_CJK_FONT_FALLBACK = "Helvetica"


def _register_cjk_font() -> str:
    """嘗試註冊中文字體，回傳可用字體名稱。"""
    global _FONT_REGISTERED, _CJK_FONT_NAME

    if _FONT_REGISTERED:
        return _CJK_FONT_NAME

    candidate_paths = [
        "C:/Windows/Fonts/msjh.ttc",
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simsun.ttc",
        "C:/Windows/Fonts/mingliu.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",
    ]

    for font_path in candidate_paths:
        try:
            pdfmetrics.registerFont(TTFont(_CJK_FONT_NAME, font_path, subfontIndex=0))
            _FONT_REGISTERED = True
            logger.info("[PDF] Registered CJK font: %s", font_path)
            return _CJK_FONT_NAME
        except Exception:
            continue

    logger.warning("[PDF] No CJK font found, falling back to Helvetica")
    _CJK_FONT_NAME = _CJK_FONT_FALLBACK
    _FONT_REGISTERED = True
    return _CJK_FONT_NAME


def _build_styles(font_name: str) -> dict[str, ParagraphStyle]:
    """建立 PDF 用的段落樣式集。"""
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "HoyaTitle", parent=base["Title"], fontName=font_name,
            fontSize=20, leading=28, spaceAfter=12,
            textColor=colors.HexColor("#1a1a2e"),
        ),
        "heading": ParagraphStyle(
            "HoyaHeading", parent=base["Heading2"], fontName=font_name,
            fontSize=14, leading=20, spaceBefore=16, spaceAfter=8,
            textColor=colors.HexColor("#16213e"),
        ),
        "subheading": ParagraphStyle(
            "HoyaSubheading", parent=base["Heading3"], fontName=font_name,
            fontSize=11, leading=16, spaceBefore=10, spaceAfter=4,
            textColor=colors.HexColor("#2d3748"),
        ),
        "body": ParagraphStyle(
            "HoyaBody", parent=base["Normal"], fontName=font_name,
            fontSize=10, leading=15, spaceAfter=6,
            textColor=colors.HexColor("#333333"),
        ),
        "bullet": ParagraphStyle(
            "HoyaBullet", parent=base["Normal"], fontName=font_name,
            fontSize=10, leading=15, leftIndent=20, spaceAfter=4,
            bulletIndent=10, textColor=colors.HexColor("#333333"),
        ),
        "mono": ParagraphStyle(
            "HoyaMono", parent=base["Code"], fontName="Courier",
            fontSize=8, leading=11, spaceAfter=2,
            textColor=colors.HexColor("#1a1a2e"),
        ),
        "meta": ParagraphStyle(
            "HoyaMeta", parent=base["Normal"], fontName=font_name,
            fontSize=9, leading=13, textColor=colors.HexColor("#666666"),
        ),
        "disclaimer": ParagraphStyle(
            "HoyaDisclaimer", parent=base["Normal"], fontName=font_name,
            fontSize=8, leading=11, textColor=colors.HexColor("#999999"),
            spaceBefore=20,
        ),
    }


def _header_footer_factory(subtitle: str):
    """建立帶有自訂副標題的頁首/頁尾繪製函式。"""
    def _draw(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        # Header bar
        canvas.setFillColor(colors.HexColor("#1a1a2e"))
        canvas.rect(0, A4[1] - 1.8 * cm, A4[0], 1.8 * cm, fill=1, stroke=0)
        canvas.setFillColor(colors.HexColor("#f0c27f"))
        canvas.setFont("Helvetica-Bold", 14)
        canvas.drawString(1.5 * cm, A4[1] - 1.3 * cm, "AlphaSonar")
        canvas.setFillColor(colors.white)
        canvas.setFont("Helvetica", 9)
        canvas.drawString(5 * cm, A4[1] - 1.3 * cm, subtitle)
        now_str = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
        canvas.drawRightString(A4[0] - 1.5 * cm, A4[1] - 1.3 * cm, now_str)
        # Footer
        canvas.setFillColor(colors.HexColor("#999999"))
        canvas.setFont("Helvetica", 8)
        canvas.drawString(1.5 * cm, 1 * cm,
                          "Generated by AlphaSonar Analysis Agent")
        canvas.drawRightString(A4[0] - 1.5 * cm, 1 * cm, f"Page {doc.page}")
        canvas.restoreState()
    return _draw


def _make_doc(buffer: io.BytesIO) -> SimpleDocTemplate:
    """建立標準 PDF 文件模板。"""
    return SimpleDocTemplate(
        buffer, pagesize=A4,
        topMargin=2.5 * cm, bottomMargin=2 * cm,
        leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )


# ═══════════════════════════════════════════════════════════════════
# Chart embedding — 兩種 Figure.kind 各自的嵌入方式
# ═══════════════════════════════════════════════════════════════════

def _fetch_image_bytes(url: str) -> bytes:
    """下載外部圖表圖片的 bytes。

    獨立成一個函式，好讓測試能直接 monkeypatch 這一行，不必真的連網
    也不必假造 HTTP transport ——PDF 生成是同步的，跟 evidence source
    那層的 `httpx.AsyncClient` 注入不是同一個接縫。
    """
    resp = httpx.get(url, timeout=10.0, follow_redirects=True)
    resp.raise_for_status()
    return resp.content


def _embed_generated_svg(fig: Figure, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """把自繪 SVG（data_uri，來自 candlestick_builder.py 等）轉成 drawing 嵌入。"""
    flowables: list[Any] = []
    try:
        import base64
        import tempfile
        # data_uri = "data:image/svg+xml;base64,..."
        b64_part = fig.data_uri.split(",", 1)[1] if fig.data_uri and "," in fig.data_uri else ""
        svg_bytes = base64.b64decode(b64_part)
        # svglib 需要檔案路徑，不支援 BytesIO
        with tempfile.NamedTemporaryFile(suffix=".svg", delete=False) as tmp:
            tmp.write(svg_bytes)
            tmp_path = tmp.name
        from svglib.svglib import svg2rlg
        drawing = svg2rlg(tmp_path)
        os.unlink(tmp_path)  # 清理暫存檔
        if drawing:
            # 縮放到頁面寬度
            scale = min(16 * cm / drawing.width, 1.0)
            drawing.width *= scale
            drawing.height *= scale
            drawing.scale(scale, scale)
            flowables.append(drawing)
            if fig.caption:
                flowables.append(Paragraph(f"<i>{_escape(fig.caption)}</i>", styles["meta"]))
            flowables.append(Spacer(1, 8))
    except Exception as exc:
        logger.warning("[PDF] SVG embed failed: %s", exc)
        # svglib 不可用或 SVG 解析失敗 — 只顯示 caption
        if fig.caption:
            flowables.append(Paragraph(f"[Chart: {_escape(fig.caption)}]", styles["meta"]))
    return flowables


def _embed_external_figure(fig: Figure, styles: dict[str, ParagraphStyle]) -> list[Any]:
    """下載外部圖表圖片並嵌入 —— 跟聊天室 `<img src=source_url>` 看到同一張圖。

    製圖正確性由原始來源負責，PDF 只忠實呈現並標明「外部圖表引用」，
    跟 `report_enhanced.py::_figures_section` 的溯源語意保持一致
    （見 CONTEXT.md「證據面」一節：自繪圖可重算，外部圖只是引用）。
    """
    flowables: list[Any] = []
    try:
        assert fig.source_url is not None
        img_bytes = _fetch_image_bytes(fig.source_url)
        reader = ImageReader(io.BytesIO(img_bytes))
        native_w, native_h = reader.getSize()
        scale = min(16 * cm / native_w, 1.0) if native_w else 1.0
        flowables.append(
            Image(io.BytesIO(img_bytes), width=native_w * scale, height=native_h * scale)
        )
        caption = (
            f"{fig.caption}（外部圖表引用，製圖正確性由原始來源負責）"
            if fig.caption else "（外部圖表引用）"
        )
        flowables.append(Paragraph(f"<i>{_escape(caption)}</i>", styles["meta"]))
        flowables.append(Spacer(1, 8))
    except Exception as exc:
        logger.warning("[PDF] External figure embed failed: %s", exc)
        # 下載失敗（斷線、404、非圖片內容）— 退回只顯示 caption + 原圖連結
        caption = fig.caption or "外部圖表"
        flowables.append(Paragraph(
            f"[外部圖表：{_escape(caption)}] 原圖：{_escape(fig.source_url or '')}",
            styles["meta"],
        ))
    return flowables


# ═══════════════════════════════════════════════════════════════════
# 1. Analysis Report PDF
# ═══════════════════════════════════════════════════════════════════

def generate_report_pdf(outcome: AnalysisOutcome) -> bytes:
    """將 AnalysisOutcome 渲染為分析報告 PDF。"""
    buffer = io.BytesIO()
    font_name = _register_cjk_font()
    styles = _build_styles(font_name)
    doc = _make_doc(buffer)
    hf = _header_footer_factory("Analysis Report")
    story: list[Any] = []
    report = outcome.report

    if report is None:
        reason = outcome.rejection.reason if outcome.rejection else "Unknown"
        story.append(Paragraph("Analysis Rejected", styles["title"]))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"Reason: {_escape(reason)}", styles["body"]))
        doc.build(story, onFirstPage=hf, onLaterPages=hf)
        return buffer.getvalue()

    story.append(Paragraph(f"{report.asset.value} Analysis Report", styles["title"]))
    story.append(Spacer(1, 6))
    story.append(Paragraph(f"Question: {_escape(report.question)}", styles["meta"]))
    story.append(Paragraph(f"Run ID: {outcome.run_id}", styles["meta"]))
    story.append(Spacer(1, 12))

    # Direction & Confidence
    story.append(Paragraph("Direction &amp; Confidence", styles["heading"]))
    from hoyabit_agent.domain import InsufficientEvidence
    conf_text = ("Insufficient evidence" if isinstance(report.confidence, InsufficientEvidence)
                 else f"{report.confidence.value:.2f} (cross-facet agreement)")
    meta_data = [
        ["Stance", report.stance.value],
        ["Confidence", conf_text],
        ["Evidence Count", str(len(report.evidence))],
        ["Claims", str(len(report.claims))],
    ]
    t = Table(meta_data, colWidths=[4 * cm, 12 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#16213e")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6), ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    # Facet Breakdown
    story.append(Paragraph("Evidence Facet Breakdown", styles["heading"]))
    counts = Counter(item.facet for item in report.evidence)
    facet_data = [["Facet", "Stance", "Evidence Count"]]
    for facet, stance in sorted(
        report.confidence.facet_stances.items(), key=lambda kv: kv[0].value
    ):
        facet_data.append([facet.value, stance.value, str(counts[facet])])
    ft = Table(facet_data, colWidths=[5 * cm, 5 * cm, 4 * cm])
    ft.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name), ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5), ("TOPPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(ft)
    story.append(Spacer(1, 12))

    # Charts — 聊天室（FigureGallery）跟 enhanced_report_md 兩者都呈現
    # generated（自繪 SVG）與 external（chart_ocr.py 等來源引用的外部圖表）
    # 兩種 kind；PDF 之前只認 generated，external 連 caption 都看不到。
    figures = [
        fig for item in report.evidence for fig in (item.figures or ())
    ]
    if figures:
        story.append(Paragraph("Technical Charts", styles["heading"]))
        for fig in figures[:6]:  # 最多 6 張圖（比較題兩幣各3張）
            if fig.kind.value == "generated" and fig.data_uri:
                story.extend(_embed_generated_svg(fig, styles))
            elif fig.kind.value == "external" and fig.source_url:
                story.extend(_embed_external_figure(fig, styles))
        story.append(Spacer(1, 12))

    # Claims — claim.text 常內嵌 Markdown 表格，用 _render_claim_flowables
    # 拆成 bullet 段落 + 真的 Table，而不是整段當純文字塞進一個 Paragraph。
    story.append(Paragraph("Claims (Evidence-backed)", styles["heading"]))
    for claim in report.claims:
        story.extend(_render_claim_flowables(
            claim.text, claim.evidence_ids, styles, font_name))
        story.append(Spacer(1, 8))
    story.append(Spacer(1, 4))

    # Limitations
    if report.limitations:
        story.append(Paragraph("Limitations &amp; Uncertainties", styles["heading"]))
        for lim in report.limitations:
            story.append(Paragraph(f"&bull; {_escape(lim)}", styles["bullet"]))
        story.append(Spacer(1, 12))

    story.append(Paragraph(
        "DISCLAIMER: This report is generated by an AI evidence analysis system. "
        "It does not constitute financial advice.", styles["disclaimer"]))

    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════
# 2. Evidence List PDF
# ═══════════════════════════════════════════════════════════════════

def generate_evidence_pdf(outcome: AnalysisOutcome) -> bytes:
    """將證據清單渲染為表格化的 PDF。"""
    buffer = io.BytesIO()
    font_name = _register_cjk_font()
    styles = _build_styles(font_name)
    doc = _make_doc(buffer)
    hf = _header_footer_factory("Evidence List")
    story: list[Any] = []

    story.append(Paragraph("Evidence List", styles["title"]))
    story.append(Paragraph(f"Run ID: {outcome.run_id}", styles["meta"]))
    story.append(Spacer(1, 12))

    report = outcome.report
    if report is None or not report.evidence:
        story.append(Paragraph("No evidence collected.", styles["body"]))
        doc.build(story, onFirstPage=hf, onLaterPages=hf)
        return buffer.getvalue()

    story.append(Paragraph(
        f"Total: {len(report.evidence)} evidence items", styles["body"]))
    story.append(Spacer(1, 8))

    # 逐項證據排版
    for i, ev in enumerate(report.evidence, 1):
        story.append(Paragraph(
            f"Evidence #{i}: {_escape(ev.id)}", styles["subheading"]))

        # 基本資訊表格
        info_data = [
            ["Facet", ev.facet.value],
            ["Summary", _truncate(ev.summary, 80)],
            ["Stance Hint", f"{ev.stance_hint:+.2f}"],
            ["Event Key", ev.event_key or "N/A"],
        ]
        info_table = Table(info_data, colWidths=[3.5 * cm, 13 * cm])
        info_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 9),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#16213e")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
        ]))
        story.append(info_table)

        # Sources 子表格
        if ev.excerpts:
            story.append(Spacer(1, 4))
            src_data = [["Source", "Fetched At", "Excerpt"]]
            for exc in ev.excerpts:
                src_data.append([
                    _truncate(exc.source_id, 35),
                    exc.retrieved_at.strftime("%Y-%m-%d %H:%M"),
                    _truncate(exc.text, 60),
                ])
            src_table = Table(src_data, colWidths=[5 * cm, 3.5 * cm, 8 * cm])
            src_table.setStyle(TableStyle([
                ("FONTNAME", (0, 0), (-1, -1), "Courier"),
                ("FONTSIZE", (0, 0), (-1, -1), 7),
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0")),
                ("FONTNAME", (0, 0), (-1, 0), font_name),
                ("FONTSIZE", (0, 0), (-1, 0), 8),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d0d0d0")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
            ]))
            story.append(src_table)

        story.append(Spacer(1, 10))

    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════
# 3. Execution Trace PDF
# ═══════════════════════════════════════════════════════════════════

def generate_trace_pdf(outcome: AnalysisOutcome) -> bytes:
    """將 ReAct 推理日誌渲染為等寬字型 PDF。"""
    buffer = io.BytesIO()
    font_name = _register_cjk_font()
    styles = _build_styles(font_name)
    doc = _make_doc(buffer)
    hf = _header_footer_factory("Execution Trace")
    story: list[Any] = []

    story.append(Paragraph("Execution Trace", styles["title"]))
    story.append(Paragraph(f"Run ID: {outcome.run_id}", styles["meta"]))
    story.append(Paragraph(
        f"Total Steps: {len(outcome.trace.nodes)}", styles["meta"]))
    story.append(Spacer(1, 12))

    for node in outcome.trace.nodes:
        # Step header
        elapsed = f"{node.elapsed_seconds:.2f}s"
        story.append(Paragraph(
            f"Step {node.seq} [{node.kind.value.upper()}] @ {elapsed}",
            styles["subheading"],
        ))

        # Reason (思考內容)
        story.append(Paragraph(
            f"<b>Thought:</b> {_escape(_truncate(node.reason, 200))}",
            styles["body"],
        ))

        # Gap info
        if node.gap_before:
            gap_str = ", ".join(sorted(f.value for f in node.gap_before))
            story.append(Paragraph(
                f"<b>Gap Before:</b> {_escape(gap_str)}", styles["mono"]))
        if node.gap_after:
            gap_str = ", ".join(sorted(f.value for f in node.gap_after))
            story.append(Paragraph(
                f"<b>Gap After:</b> {_escape(gap_str)}", styles["mono"]))

        # Tool executions (Action / Observation)
        if node.executions:
            for ex in node.executions:
                args_str = json.dumps(
                    dict(ex.arguments) if ex.arguments else {},
                    ensure_ascii=False,
                )
                story.append(Paragraph(
                    f"<b>Action:</b> {_escape(ex.tool)}"
                    f"({_escape(_truncate(args_str, 100))})",
                    styles["mono"],
                ))
                story.append(Paragraph(
                    f"<b>Result:</b> {_escape(ex.status.value)}",
                    styles["mono"],
                ))

        # Evidence collected
        if node.evidence_ids:
            ev_str = ", ".join(node.evidence_ids)
            story.append(Paragraph(
                f"<b>Evidence:</b> {_escape(ev_str)}", styles["mono"]))

        story.append(Spacer(1, 8))

    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════
# 4. Agent Config PDF
# ═══════════════════════════════════════════════════════════════════

def generate_config_pdf(outcome: AnalysisOutcome, session_id: str | None = None) -> bytes:
    """將 Agent 系統配置渲染為美化排版的 PDF。"""
    buffer = io.BytesIO()
    font_name = _register_cjk_font()
    styles = _build_styles(font_name)
    doc = _make_doc(buffer)
    hf = _header_footer_factory("Agent Configuration")
    story: list[Any] = []

    story.append(Paragraph("Agent Configuration", styles["title"]))
    story.append(Paragraph(f"Run ID: {outcome.run_id}", styles["meta"]))
    if session_id:
        story.append(Paragraph(f"Session ID: {session_id}", styles["meta"]))
    story.append(Spacer(1, 12))

    # Agent Info
    story.append(Paragraph("Agent Identity", styles["heading"]))
    agent_data = [
        ["Name", "AlphaSonar Crypto Evidence Agent"],
        ["Version", "0.1.0"],
        ["Architecture", "ReAct (Reasoning + Acting)"],
    ]
    at = Table(agent_data, colWidths=[4 * cm, 12.5 * cm])
    at.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#16213e")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, -1), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
    ]))
    story.append(at)
    story.append(Spacer(1, 10))

    # Model Configuration
    story.append(Paragraph("Model Configuration", styles["heading"]))
    model_data = [
        ["Provider", os.environ.get("MODEL_PROVIDER", "bedrock")],
        ["Model", os.environ.get("BEDROCK_MODEL", "us.anthropic.claude-sonnet-4-6")],
        ["Region", os.environ.get("BEDROCK_REGION", "us-east-1")],
    ]
    mt = Table(model_data, colWidths=[4 * cm, 12.5 * cm])
    mt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#16213e")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(mt)
    story.append(Spacer(1, 10))

    # Analysis Parameters
    story.append(Paragraph("Analysis Parameters", styles["heading"]))
    params_data = [
        ["Parameter", "Value"],
        ["Max Iterations", "6"],
        ["Budget (seconds)", "900"],
        ["I/O Timeout (seconds)", "15"],
        ["Assembly Reserve (seconds)", "120"],
        ["Cache TTL - Athena", "300s"],
        ["Cache TTL - Kinesis", "60s"],
        ["Covered Assets", "BTC, ETH, SOL, BNB, XRP"],
    ]
    pt = Table(params_data, colWidths=[5.5 * cm, 11 * cm])
    pt.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f5f5f5")),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e0e0e0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(pt)
    story.append(Spacer(1, 10))

    # Evidence Sources
    story.append(Paragraph("Registered Evidence Sources", styles["heading"]))
    sources = [
        "binance_spot (Binance Spot Market OHLCV + Order Book)",
        "binance_derivatives (Funding Rate, OI, Long/Short Ratio)",
        "crypto_news (CoinDesk, Cointelegraph RSS)",
        "extended_news (Blocktempo, Blockworks)",
        "official_announcements (Project Blogs)",
        "coingecko_market (Market Cap, Volume, Price Changes)",
        "defillama_tvl (On-chain TVL by Chain)",
        "fear_greed_index (Crypto Fear & Greed 0-100)",
        "fred_macro (Fed Funds Rate, M2, CPI, DXY, 10Y Treasury)",
        "candlestick_chart_builder (SVG K-line Charts)",
        "ocr_chart_extractor (Claude Vision Chart Analysis)",
        "market_dataset_context (Competition OHLCV Dataset)",
    ]
    for src in sources:
        story.append(Paragraph(f"&bull; {_escape(src)}", styles["bullet"]))
    story.append(Spacer(1, 10))

    # System Prompt Summary
    story.append(Paragraph("System Prompt (Summary)", styles["heading"]))
    prompt_text = (
        "Evidence-based crypto analysis agent operating under strict rules: "
        "(1) No claim enters the report without backing evidence. "
        "(2) Asset gate is whitelist-only (BTC/ETH/SOL/BNB/XRP). "
        "(3) Confidence is cross-facet agreement, not model self-assessment. "
        "(4) Republication does not constitute independent evidence. "
        "(5) Data source failure is expressed as empty set, never exception. "
        "(6) Never fail due to timeout - return report with available evidence. "
        "(7) Tool boundary: external I/O = MCP tool; pure logic = Function tool."
    )
    story.append(Paragraph(_escape(prompt_text), styles["body"]))

    # Analysis Result (if available)
    if outcome.report is not None:
        story.append(Spacer(1, 10))
        story.append(Paragraph("Analysis Result Summary", styles["heading"]))
        r = outcome.report
        result_data = [
            ["Asset", r.asset.value],
            ["Stance", r.stance.value],
            ["Question", _truncate(r.question, 60)],
            ["Claims", str(len(r.claims))],
            ["Evidence Items", str(len(r.evidence))],
        ]
        rt = Table(result_data, colWidths=[4 * cm, 12.5 * cm])
        rt.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, -1), font_name),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("TEXTCOLOR", (0, 0), (0, -1), colors.HexColor("#16213e")),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(rt)

    doc.build(story, onFirstPage=hf, onLaterPages=hf)
    return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

def _escape(text: str) -> str:
    """Escape XML special characters for reportlab Paragraph."""
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _truncate(text: str, max_len: int = 80) -> str:
    """截斷過長文字，附加省略號。"""
    if len(text) <= max_len:
        return text
    return text[:max_len - 3] + "..."


# ═══════════════════════════════════════════════════════════════════
# Claim text 裡的 Markdown 表格 —— 對應 chat-thread.tsx 的 markdownToHtml
# ═══════════════════════════════════════════════════════════════════
#
# claim.text 常常內嵌 5-7 日簡表（近期指標時序）。PDF 之前直接把整段
# claim.text 一起 _escape() 塞進單一 Paragraph，表格語法完全沒被解析，
# 管線符號原封不動印出來——跟 chat-thread.tsx 修過的那個 bug是同一類，
# 只是這邊還沒修。前端跟這裡是兩個不同的執行環境（瀏覽器 JS vs
# ReportLab），程式碼沒辦法真的共用，但「判斷規則」刻意保持一致：
# 一列是不是「標題列」只看它的下一行是不是 |---|---| 分隔線，跟內容
# 本身無關（跟 markdownToHtml 的判斷邏輯對稱）。

_TABLE_ROW_PATTERN = re.compile(r"^\|(.+)\|$")
_SEPARATOR_CELL_PATTERN = re.compile(r"^[-:]+$")


@dataclass(frozen=True)
class _ParsedTableRow:
    cells: tuple[str, ...]
    is_header: bool


def _is_table_row(line: str) -> bool:
    return bool(_TABLE_ROW_PATTERN.match(line.strip()))


def _split_row_cells(line: str) -> list[str]:
    stripped = line.strip()
    return [c.strip() for c in stripped[1:-1].split("|")]


def _is_separator_row(cells: list[str]) -> bool:
    return bool(cells) and all(_SEPARATOR_CELL_PATTERN.match(c) for c in cells)


def _extract_markdown_segments(text: str) -> list[str | list[_ParsedTableRow]]:
    """把 claim.text 依行走訪，拆成「純文字段落」與「表格 rows」，保留原始順序。"""
    lines = text.split("\n")
    segments: list[str | list[_ParsedTableRow]] = []
    prose_buffer: list[str] = []
    table_buffer: list[_ParsedTableRow] = []

    def flush_prose() -> None:
        joined = "\n".join(prose_buffer).strip()
        if joined:
            segments.append(joined)
        prose_buffer.clear()

    def flush_table() -> None:
        if table_buffer:
            segments.append(list(table_buffer))
            table_buffer.clear()

    for i, line in enumerate(lines):
        if not _is_table_row(line):
            flush_table()
            prose_buffer.append(line)
            continue

        cells = _split_row_cells(line)
        if _is_separator_row(cells):
            continue  # 分隔線本身不帶內容，只用來判斷上一列是不是標題列

        flush_prose()
        next_line = lines[i + 1] if i + 1 < len(lines) else ""
        is_header = _is_table_row(next_line) and _is_separator_row(
            _split_row_cells(next_line)
        )
        table_buffer.append(_ParsedTableRow(cells=tuple(cells), is_header=is_header))

    flush_table()
    flush_prose()
    return segments


def _build_table_flowable(rows: list[_ParsedTableRow], font_name: str) -> Table:
    data = [list(r.cells) for r in rows]
    t = Table(data, hAlign="LEFT", repeatRows=1 if rows and rows[0].is_header else 0)
    style_commands: list[tuple[Any, ...]] = [
        ("FONTNAME", (0, 0), (-1, -1), font_name),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    for idx, row in enumerate(rows):
        if row.is_header:
            style_commands.append(
                ("BACKGROUND", (0, idx), (-1, idx), colors.HexColor("#f5f5f5"))
            )
    t.setStyle(TableStyle(style_commands))
    return t


def _render_claim_flowables(
    text: str, evidence_ids: tuple[str, ...], styles: dict[str, ParagraphStyle], font_name: str
) -> list[Any]:
    """把一則 claim 轉成一串 flowables —— 表格段落畫成真的 Table，其餘畫成 bullet paragraph。"""
    segments = _extract_markdown_segments(text)
    flowables: list[Any] = []
    for i, seg in enumerate(segments):
        if isinstance(seg, str):
            prefix = "&bull; " if i == 0 else ""
            flowables.append(Paragraph(f"{prefix}{_escape(seg)}", styles["bullet"]))
        else:
            flowables.append(_build_table_flowable(seg, font_name))
            flowables.append(Spacer(1, 6))
    if not flowables:
        flowables.append(Paragraph("&bull; (empty)", styles["bullet"]))
    citations = ", ".join(evidence_ids)
    flowables.append(Paragraph(f"<i>[{_escape(citations)}]</i>", styles["mono"]))
    return flowables


__all__ = [
    "generate_report_pdf",
    "generate_evidence_pdf",
    "generate_trace_pdf",
    "generate_config_pdf",
]
