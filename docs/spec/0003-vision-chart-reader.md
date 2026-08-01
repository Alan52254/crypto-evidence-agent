# Spec 0003 — Vision Chart Reader + Web Chart Capture

## 1. Requirements

### 1.1 背景

當前系統僅能處理純文字資料源。大量關鍵數據（M2 折線圖、Fed 點陣圖、交易所持倉圖）存在於圖表中，無法擷取。

### 1.2 功能需求

| ID | 需求 | 優先級 |
|----|------|--------|
| R1 | Agent 能接收圖片並擷取結構化數據（X/Y 軸、趨勢） | P0 |
| R2 | 圖表解析結果作為 Evidence 進入證據管線 | P0 |
| R3 | 解析度不佳時誠實標註「僅趨勢判斷」 | P0 |
| R4 | Agent 能自主去預定義頁面截圖取得圖表 | P0 |
| R5 | 使用者上傳圖片至 /input_charts/ 自動觸發分析 | P1 |
| R6 | 不得對無法辨識的數值補值或臆測 | P0（鐵則） |

### 1.3 非功能需求

- 單圖解析 < 8 秒、截圖 < 5 秒
- 每回合最多 5 張圖
- 數據表格 OCR 準確率 > 95%；趨勢方向正確率 > 90%

### 1.4 不做的事

- 不做需要登入/付費牆的頁面截圖
- 不替代已有結構化 API 的來源
- 不渲染需要使用者互動才顯示的元件

---

## 2. Technical Design

### 2.1 三層圖表數據取得策略

```
Layer 1（最優先）：結構化 API（Binance、DeFiLlama、CoinGecko）
Layer 2（次級）  ：Agent 自動截圖 → chart_reader 解析
Layer 3（補充）  ：使用者手動上傳圖表
```

Agent 決策流程：
```
提問涉及圖表數據
  ├─ 有結構化 API？ → 用 API（Layer 1）
  ├─ CHART_REGISTRY 有對應？ → web_chart_capture 截圖 + 解析（Layer 2）
  └─ 都沒有 → 標註「證據不足」+ Fallback 新聞關鍵字搜尋
```

### 2.2 新增模組

| 模組 | 類型 | 職責 |
|------|------|------|
| `models/vision.py` | 內部 | VisionModelAdapter — Gemini 多模態圖表解析 |
| `sources/chart_reader.py` | MCP Tool | 接收圖片 → 呼叫 Vision → 產出 Evidence |
| `sources/web_chart_capture.py` | MCP Tool | Playwright 截圖 → 送 chart_reader |

### 2.3 web_chart_capture — 預定義圖表來源對照表

```python
CHART_REGISTRY = {
    # 宏觀經濟
    "us_m2_supply":       FRED M2 貨幣供給量月線圖
    "us_fed_funds_rate":  FRED 聯邦基金利率
    "us_cpi_yoy":         FRED CPI 年增率
    "dxy_index":          TradingView 美元指數 DXY

    # 鏈上 / 交易所
    "btc_exchange_reserve": Coinglass BTC 交易所儲備量
    "btc_etf_flow":         Coinglass 美國 BTC ETF 每日淨流入/流出
    "eth_etf_flow":         Coinglass 美國 ETH ETF 每日淨流入/流出

    # 衍生品
    "btc_funding_rate":   Coinglass BTC 資金費率歷史
    "btc_open_interest":  Coinglass BTC OI 趨勢
    "liquidation_heatmap": Coinglass 全網爆倉熱力圖

    # 鏈上活動
    "eth_gas_burned":     ultrasound.money ETH 燃燒量
    "defi_tvl_overview":  DeFiLlama 全鏈 TVL 總覽
}
```

每筆包含：url、CSS selector（定位圖表區域）、description、facet、wait_ms。

### 2.4 VisionModelAdapter — 結構化輸出

```python
@dataclass(frozen=True)
class ChartDataPoint:
    x_label: str           # 日期或類別
    y_value: float | None  # 無法辨識時為 None
    annotation: str = ""

@dataclass(frozen=True)
class ChartAnalysis:
    chart_type: str        # line / candlestick / bar / dot_plot / table / heatmap
    title: str
    x_axis_label: str
    y_axis_label: str
    data_points: tuple[ChartDataPoint, ...]
    trend_direction: str   # up / down / sideways / unclear
    trend_summary: str     # 一句話描述
    confidence: float      # 0-1
    raw_description: str   # 模型原始描述（溯源用）
```

利用 Gemini 2.5 Flash 原生 `inline_data` image input，不需換模型或加新 key。

### 2.5 信心度與降級規則

| confidence | 處理 |
|-----------|------|
| >= 0.8 | 正常呈現數據點 |
| 0.5 - 0.8 | 前綴「（圖表解析）」 |
| 0.3 - 0.5 | 前綴「⚠️ 僅趨勢判斷」，stance_hint 衰減 |
| < 0.3 | 不產出 Evidence（回傳空集合） |

### 2.6 Steering Rule（`.kiro/steering/vision_agent.md`）

1. 讀圖時必須標註數據來源與數據點，不可臆測
2. 解析度不佳時聲明「僅趨勢判斷」，不得產生幻覺數值
3. 圖表 Evidence ID 前綴 CHART-
4. 能用 API 就不讀圖
5. 每回合最多 5 張圖
6. web_chart_capture 只對預定義來源截圖
7. 截圖失敗以空集合表達

### 2.7 Agent Hook

PostFileCreate trigger，匹配 `input_charts/.*\.(png|jpg|jpeg|webp)$`，
自動觸發 chart_reader 分析上傳的圖表。

### 2.8 新增依賴

```toml
[project.optional-dependencies]
vision = ["playwright>=1.48", "Pillow>=10.0"]
```

安裝後需 `playwright install chromium`。

---

## 3. 風險與取捨

| 風險 | 緩解 |
|------|------|
| K 線圖數值擷取不穩定 | confidence < 0.5 降級為趨勢判斷 |
| 圖片 token 成本 | 限 5 張 + 壓縮至 1024px |
| 15 分鐘預算 | 截圖 5s + 解析 8s = 13s/張，5 張 ≈ 65s |
| 頁面改版 selector 失效 | 集中管理 + 失效回傳空集合 |
| Playwright 安裝包大 | optional dependency `[vision]` |

---

## 4. 實作順序

1. `models/vision.py` — ChartAnalysis + VisionModelAdapter
2. `sources/chart_reader.py` — 圖片解析 MCP Tool
3. `sources/web_chart_capture.py` — Playwright 截圖 + CHART_REGISTRY
4. `tools.py` — 註冊兩工具
5. `models/prompts.py` — PLAN_SYSTEM 第 12 條圖表策略
6. `.kiro/steering/vision_agent.md`
7. `.kiro/hooks/chart-auto-read.json`
8. `pyproject.toml` — `[vision]` dependency
9. 測試
