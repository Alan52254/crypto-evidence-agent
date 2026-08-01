"""PDF 產生器 —— 外部圖表引用必須跟自繪 SVG 圖表一樣真的被嵌進下載的 PDF。

`chart_ocr.py`（以及其他視覺抽取來源）產出的是 `kind=EXTERNAL`、只有
`source_url` 沒有 `data_uri` 的 Figure。聊天室（`FigureGallery`）跟
`enhanced_report_md`（`report_enhanced.py::_figures_section`）都會呈現這種圖，
PDF 之前的篩選條件卻只認 `kind=="generated" and data_uri`，把外部圖整個
濾掉——連 caption 都看不到。這裡守住「PDF 跟聊天室看到一樣的圖」。
"""

from __future__ import annotations

import base64
import io
from dataclasses import replace

from pypdf import PdfReader

from hoyabit_agent import pdf_generator
from hoyabit_agent.domain import (
    AnalysisOutcome,
    Asset,
    Claim,
    Confidence,
    Facet,
    Figure,
    FigureKind,
    Report,
    Stance,
    Trace,
)
from hoyabit_agent.testing import evidence

# 1x1 透明 PNG —— 最小的合法點陣圖，讓 reportlab.Image 有真的尺寸可讀。
_ONE_PIXEL_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _outcome_with(figure: Figure) -> AnalysisOutcome:
    item = replace(evidence("E1", Facet.TECHNICAL, 0.5), figures=(figure,))
    report = Report(
        asset=Asset.BTC,
        stance=Stance.BULLISH,
        confidence=Confidence(value=0.8, facet_stances={f: Stance.NEUTRAL for f in Facet}),
        claims=(Claim("測試判斷", ("E1",), Facet.TECHNICAL),),
        dropped_claims=(),
        evidence=(item,),
    )
    return AnalysisOutcome(
        run_id="run-pdf-1",
        report=report,
        trace=Trace(run_id="run-pdf-1", nodes=()),
        rejection=None,
    )


def _embedded_image_count(pdf_bytes: bytes) -> int:
    reader = PdfReader(io.BytesIO(pdf_bytes))
    return sum(len(page.images) for page in reader.pages)


def test_external_figures_are_embedded_as_images(monkeypatch) -> None:
    """外部圖表引用（chart_ocr.py 的證據）要真的出現在 PDF 裡，不只是文字說明。"""
    monkeypatch.setattr(pdf_generator, "_fetch_image_bytes", lambda url: _ONE_PIXEL_PNG)

    figure = Figure(
        kind=FigureKind.EXTERNAL,
        caption="外部圖表：Glassnode 交易所儲備",
        source_url="https://example.test/chart.png",
        alt="external chart",
    )
    outcome = _outcome_with(figure)

    pdf_bytes = pdf_generator.generate_report_pdf(outcome)

    assert _embedded_image_count(pdf_bytes) >= 1


def test_external_figure_fetch_failure_falls_back_to_caption_text(monkeypatch) -> None:
    """外部圖下載失敗時（斷線、404、非圖片內容）不能讓整份 PDF 生成失敗，
    退回目前既有的「只顯示 caption」模式，並附上原圖連結方便追查。
    """

    def _raise(url: str) -> bytes:
        raise ValueError("boom")

    monkeypatch.setattr(pdf_generator, "_fetch_image_bytes", _raise)

    figure = Figure(
        kind=FigureKind.EXTERNAL,
        caption="外部圖表：下載會失敗",
        source_url="https://example.test/broken.png",
        alt="external chart",
    )
    outcome = _outcome_with(figure)

    pdf_bytes = pdf_generator.generate_report_pdf(outcome)

    assert _embedded_image_count(pdf_bytes) == 0
    reader = PdfReader(io.BytesIO(pdf_bytes))
    text = "\n".join(page.extract_text() for page in reader.pages)
    assert "下載會失敗" in text
    assert "example.test/broken.png" in text
