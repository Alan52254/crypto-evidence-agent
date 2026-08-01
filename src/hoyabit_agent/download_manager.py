"""ZIP 打包管理器 — 將分析成果打包為 4 份 PDF 的 ZIP 檔案。

打包內容（全部為 PDF 格式）：
1. 1_analysis_report.pdf — 排版精美的分析報告
2. 2_evidence_list.pdf — 證據清單表格化 PDF
3. 3_execution_trace.pdf — Agent ReAct 推理日誌（等寬字型）
4. 4_agent_config.pdf — 系統 Prompt 與模型配置

穩健性：
- 每份 PDF 獨立生成，單一失敗不阻斷整體打包
- 失敗時 fallback 為最小純文字 PDF
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import UTC, datetime
from typing import Any

from hoyabit_agent.domain import AnalysisOutcome

logger = logging.getLogger(__name__)


def build_export_zip(outcome: AnalysisOutcome, session_id: str | None = None) -> bytes:
    """將分析成果打包為 ZIP（4 份 PDF），回傳 bytes。"""
    buffer = io.BytesIO()

    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        # 1. 分析報告 PDF
        zf.writestr("1_analysis_report.pdf", _safe_pdf(
            lambda: _gen_report(outcome), outcome, "Analysis Report"))

        # 2. 證據清單 PDF
        zf.writestr("2_evidence_list.pdf", _safe_pdf(
            lambda: _gen_evidence(outcome), outcome, "Evidence List"))

        # 3. 執行紀錄 PDF
        zf.writestr("3_execution_trace.pdf", _safe_pdf(
            lambda: _gen_trace(outcome), outcome, "Execution Trace"))

        # 4. 系統配置 PDF
        zf.writestr("4_agent_config.pdf", _safe_pdf(
            lambda: _gen_config(outcome, session_id), outcome, "Agent Config"))

    return buffer.getvalue()


def export_filename(session_id: str | None = None) -> str:
    """產出 ZIP 檔案名稱。"""
    identifier = session_id or datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    return f"HoyaBit_Analysis_{identifier}.zip"


# ─── Internal Builders ───


def _gen_report(outcome: AnalysisOutcome) -> bytes:
    from hoyabit_agent.pdf_generator import generate_report_pdf
    return generate_report_pdf(outcome)


def _gen_evidence(outcome: AnalysisOutcome) -> bytes:
    from hoyabit_agent.pdf_generator import generate_evidence_pdf
    return generate_evidence_pdf(outcome)


def _gen_trace(outcome: AnalysisOutcome) -> bytes:
    from hoyabit_agent.pdf_generator import generate_trace_pdf
    return generate_trace_pdf(outcome)


def _gen_config(outcome: AnalysisOutcome, session_id: str | None) -> bytes:
    from hoyabit_agent.pdf_generator import generate_config_pdf
    return generate_config_pdf(outcome, session_id=session_id)


def _safe_pdf(
    generator: Any,
    outcome: AnalysisOutcome,
    title: str,
) -> bytes:
    """安全呼叫 PDF 生成器；失敗時 fallback 為最小純文字 PDF。"""
    try:
        return generator()
    except Exception as exc:
        logger.warning("[DownloadManager] %s PDF failed, using fallback: %s", title, exc)
        return _fallback_pdf(title, outcome)


def _fallback_pdf(title: str, outcome: AnalysisOutcome) -> bytes:
    """最小 fallback PDF — 純文字內容。"""
    try:
        from reportlab.lib.pagesizes import A4
        from reportlab.pdfgen import canvas

        buffer = io.BytesIO()
        c = canvas.Canvas(buffer, pagesize=A4)
        c.setFont("Helvetica-Bold", 14)
        y = A4[1] - 50
        c.drawString(50, y, f"HoyaBit - {title}")
        y -= 30
        c.setFont("Helvetica", 10)
        c.drawString(50, y, f"Run ID: {outcome.run_id}")
        y -= 20
        c.drawString(50, y, f"Generated: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
        y -= 20
        c.drawString(50, y, "(PDF generation failed - minimal fallback)")
        c.save()
        return buffer.getvalue()
    except Exception:
        return b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[]/Count 0>>endobj\nxref\n0 3\n0000000000 65535 f \n0000000009 00000 n \n0000000052 00000 n \ntrailer<</Size 3/Root 1 0 R>>\nstartxref\n101\n%%EOF\n"


__all__ = ["build_export_zip", "export_filename"]
