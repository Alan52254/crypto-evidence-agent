# HoyaBit 加密貨幣分析 Agent — 完整系統規格書

> **版本**: 2026-08-01 | **分支**: feature/bedrock-provider  
> **推論引擎**: AWS Bedrock Claude Sonnet 4.6 (`us.anthropic.claude-sonnet-4-6`)  
> **資料截止**: 即時（LIVE 模式）或 2026-05-31（回測模式）

---

## 1. 系統概覽

一個 ReAct 架構的加密貨幣分析 Agent，在 **15 分鐘壁鐘預算**內：
1. 從 **12 個獨立資料源**蒐集原始市場資料
2. 使用 LLM 推論引擎產出**每句判斷都可溯源到原始片段**的分析報告
3. 以**五維信心度公式**量化結論可靠度
4. 必含反方論點、風險聲明、不確定性揭露

### 受涵蓋幣種（白名單制，非黑名單）
- BTC, ETH, SOL, BNB, XRP
- 任何不在此集合內的資產一律被閘門拒絕，不進入推論

---

## 2. 三層接縫架構

```
┌───────────────────────────────────────────────────────────────┐
│  接縫 1: 資料層 (Evidence Sources) — 有外部 I/O              │
│  MCP Tools: 12 個獨立資料源                                    │
│  判準：有沒有跨行程網路 I/O                                    │
└────────────────────────┬──────────────────────────────────────┘
                         │ tuple[Evidence, ...]
┌────────────────────────▼──────────────────────────────────────┐
│  接縫 2: 推論層 (run.analyse) — 主流程                        │
│  閘門 → 題型分類 → ReAct 蒐集 → 合成 → 引用驗證 → 組裝       │
│  Function Tools: 無 I/O 純計算                                │
└────────────────────────┬──────────────────────────────────────┘
                         │ Report / Trace
┌────────────────────────▼──────────────────────────────────────┐
│  接縫 3: 模型層 (ModelProvider Protocol)                      │
│  plan() → synthesise() → label()                              │
│  實作: BedrockProvider (Claude Sonnet 4.6)                    │
└───────────────────────────────────────────────────────────────┘
```

---

## 3. 資料來源完整清單

### 3.1 技術面 (Technical)

| 工具名稱 | API 端點 | 取得資料 | 產出指標 |
|---------|----------|---------|---------|
| `binance_spot` | `GET https://api.binance.com/api/v3/klines` | OHLCV K 線（最多 200 根） | SMA60, SMA200, RSI(14), MACD(12,26,9), KD(9,3,3), 布林通道(20,2), 成交量變化 |
| `binance_spot` | `GET https://api.binance.com/api/v3/depth` | 盤口掛單（前 20-100 檔） | 買賣失衡度, 相對價差 |
| `candlestick_chart_builder` | `GET https://api.binance.com/api/v3/klines` | 30 根 K 線 | SVG K 線圖、走勢圖、RSI 圖（base64） |
| `market_dataset_context` | 本地 CSV（競賽 OHLCV） | 歷史日線 | 相似度搜尋 + 指標計算 |

**技術指標計算公式（全部在 `indicators.py`，確定性純函數）**：

| 指標 | 公式 | 傾向映射 |
|------|------|---------|
| SMA(N) | 最近 N 根收盤價算術平均 | 收盤 vs SMA 乖離 ÷ 10% → [-1, +1] |
| RSI(14) | Wilder 平滑法 | (RSI - 50) ÷ 50 → [-1, +1] |
| MACD(12,26,9) | DIF = EMA12 - EMA26, DEA = EMA9(DIF) | Histogram 正負 |
| KD(9,3,3) | Stochastic Oscillator | K 值位置映射 |
| 布林通道(20,2) | Middle = SMA20, Band = 2σ | %B 位置映射 |
| 成交量變化 | 近 5 期均量 vs 前 5 期均量 | 百分比變化 |
| 盤口失衡 | (bid_qty - ask_qty) / total / 0.30 | 30% 偏差為滿分 |

### 3.2 籌碼面 (Positioning)

| 工具名稱 | API 端點 | 取得資料 | 傾向映射 |
|---------|----------|---------|---------|
| `binance_derivatives` | `GET https://fapi.binance.com/fapi/v1/fundingRate` | 永續合約資金費率 | rate ÷ 0.01% → [-1, +1] |
| `binance_derivatives` | `GET https://fapi.binance.com/futures/data/openInterestHist` | OI 歷史序列 | 變化率（百分比） |
| `binance_derivatives` | `GET https://fapi.binance.com/futures/data/globalLongShortAccountRatio` | 多空帳戶比 | (ratio - 1.0) ÷ 0.5 → [-1, +1] |
| `binance_spot` (depth) | `GET https://api.binance.com/api/v3/depth` | 買賣掛單深度 | 失衡度 |

### 3.3 基本面 (Fundamental)

| 工具名稱 | API 端點 | 取得資料 | 說明 |
|---------|----------|---------|------|
| `fred_macro` | `GET https://api.stlouisfed.org/fred/series/observations` | 聯邦基金利率 (FEDFUNDS) | 美國貨幣政策方向 |
| `fred_macro` | 同上 | M2 貨幣供給 (M2SL) | 全球流動性 |
| `fred_macro` | 同上 | CPI 消費者物價指數 | 通膨壓力 |
| `fred_macro` | 同上 | 美元指數 (DTWEXBGS) | 美元強弱（反向影響 crypto） |
| `fred_macro` | 同上 | 10 年期美債殖利率 (DGS10) | 無風險利率基準 |
| `coingecko_market` | `GET https://api.coingecko.com/api/v3/coins/{id}` | 市值、排名、24h/7d/30d 漲跌、流通量 | 市場結構 |
| `defillama_tvl` | `GET https://api.llama.fi/v2/chains` | 鏈上 TVL | 鏈上資金活動 |

### 3.4 情緒面 (Sentiment)

| 工具名稱 | API 端點 | 取得資料 | 說明 |
|---------|----------|---------|------|
| `fear_greed_index` | `GET https://api.alternative.me/fng/` | Crypto Fear & Greed Index (0-100) | 全市場情緒溫度計 |
| `crypto_news` | RSS: `cointelegraph.com/rss`, `coindesk.com/arc/outboundfeeds/rss` | 新聞標題 + 摘要 | LLM 情緒標註 |
| `extended_news` | RSS: `blocktempo.com/feed/`, `blockworks.co/feed/` | 繁中 + 機構觀點新聞 | 獨立交叉驗證 |
| `official_announcements` | RSS: `blog.ethereum.org`, `coindesk.com` (BTC) | 官方公告 | 第一手消息 |

### 3.5 圖表工具

| 工具名稱 | 功能 | 輸出格式 |
|---------|------|---------|
| `candlestick_chart_builder` | Binance K 線 → SVG 圖表 | base64 data URI（K 線 + 走勢 + RSI） |
| `ocr_chart_extractor` | 外部圖片 URL → Gemini Vision OCR | 結構化數據 + 原圖引用 |

---

## 4. 信心度公式（五維加權 + 懲罰機制）

### 4.1 基本五維公式

```
confidence = independence × 0.25
           + coverage × 0.25
           + freshness × 0.20
           + agreement × 0.20
           + completeness × 0.10
```

| 維度 | 計算方式 | 範圍 |
|------|---------|------|
| **獨立性 (independence)** | distinct source clusters ÷ 6（同 API call 歸同一 cluster） | 0.0 ~ 1.0 |
| **覆蓋率 (coverage)** | 有證據的面數 ÷ 4 | 0.25 ~ 1.0 |
| **新鮮度 (freshness)** | 最新證據距分析時間：≤1h=1.0, ≤24h 線性衰減, >24h=0.2 | 0.2 ~ 1.0 |
| **一致性 (agreement)** | 表態面中多數方向的比例 × 表態面比 - 矛盾懲罰(0.15/面) | 0.0 ~ 1.0 |
| **完整性 (completeness)** | min(證據項數, 15) ÷ 15 | 0.0 ~ 1.0 |

### 4.2 懲罰機制

| 懲罰類型 | 觸發條件 | 扣分 |
|---------|---------|------|
| 核心資料缺失 (CORE) | 題目主問的資料系統不具備 | 每項 -0.15 |
| 輔助資料缺失 (SUPPORTING) | 輔助資料不可用 | 每項 -0.08 |
| 部分可用 (PARTIAL) | 僅有間接替代 | 每項 -0.05 |
| 過期技術面 | 技術面只有 dataset 無即時 Binance | coverage × 0.6 |
| 爭議判斷比例 | contested/total > 50% | (ratio - 0.5) × 0.30 |

### 4.3 三態輸出

| 狀態 | 條件 | 意義 |
|------|------|------|
| `Confidence(value=X)` | 正常計算 | 有數值的信心度 |
| `InsufficientEvidence(TOO_FEW_FACETS)` | 有證據的面 < 2 | 面向太少 |
| `InsufficientEvidence(NO_DIRECTIONAL_SIGNAL)` | 表態面 < 2 或核心缺失後 < 0.25 | 無法判斷方向 |

---

## 5. 推論流程詳解

### 5.1 ReAct 蒐集迴圈

```
1. 閘門 (gate_asset) → 白名單檢查
2. 題型分類 (classify_question) → 決定需要哪些面
3. 核心資料缺失偵測 (detect_core_data_demands) → 確定性關鍵字比對
4. 預取 (prefetch) → binance_spot, coingecko, fred, defillama, fgi, candlestick 同時發
5. 缺口檢查 (evidence_gap) → 判斷哪些面還不夠
6. ReAct 迴圈:
   a. model.plan() → LLM 決定呼叫哪些工具
   b. 執行工具 → 取得新證據
   c. 缺口再檢查 → 直到所有必補缺口關閉或預算用罄
7. model.synthesise() → 從全部證據產出三層判斷 (fact/inference/conclusion)
8. review_claims() → 輕量自我審查（語氣修飾）
9. enforce_paired_disclosure() → 確定性配對揭露（SMA60/SMA200, FEDFUNDS/DGS10）
10. enforce_indicator_citations() → 掃描幻覺指標數字
11. verify (claim_ledger) → 引用檢核：每則判斷必須有 evidence_id
12. _assemble() → 限制聲明 + 信心度計算 + 爭議比例修正 → Report
```

### 5.2 判斷分層結構

| 層 | role | 規則 |
|---|------|------|
| 事實層 | `fact` | 直接引用數據，不加推論，無方向性 |
| 推論層 | `inference` | 交叉推導，引用 2+ 事實，明確說明邏輯 |
| 結論層 | `conclusion` | 最終觀點，附推翻條件 |
| 反方 | `counter_evidence` | 至少 1 則，指出反向證據 |
| 風險 | `risk` | 至少 1 則，聲明風險與不確定性 |
| 觀察 | `watch` | 至少 1 則，說明數據缺口與後續觀察點 |

### 5.3 引用檢核規則

- 每則判斷**必須**掛載至少 1 個 `evidence_id`
- 未掛載的判斷被 `claim_ledger` 丟棄（`unsupported`）
- 掛載但支撐薄弱的標為 `contested`（保留在報告但標記）
- 通過驗證的進入 `supported`（正式報告內容）

---

## 6. 模型層規格

### 6.1 當前配置

| 項目 | 值 |
|------|---|
| 推論引擎 | AWS Bedrock Claude Sonnet 4.6 |
| Model ID | `us.anthropic.claude-sonnet-4-6` |
| Region | us-east-1 |
| 認證 | Bearer token (Bedrock API Key) |
| API | Converse API (`POST /model/{id}/converse`) |
| Timeout | 180 秒 |
| maxTokens | 8192 |
| Temperature | 0.1 |
| 備用引擎 | 無（Gemini 已移除避免 429） |

### 6.2 ModelProvider 三方法

```python
class ModelProvider(Protocol):
    async def plan(context, tools) -> PlanDecision      # 決定呼叫哪些工具
    async def synthesise(asset, evidence, question) -> tuple[DraftClaim, ...]  # 產出判斷
    async def label(texts, aspect) -> tuple[float, ...]  # 批次情緒/基本面標註
```

### 6.3 情緒標註

新聞取得後由 `label()` 雙面標註：
- **Sentiment（文本傾向）**：語氣樂觀/悲觀 → [-1, +1]
- **Fundamental（事件影響）**：事件本身利多/利空 → [-1, +1]

同一篇新聞產出兩筆 Evidence（一筆 sentiment、一筆 fundamental）。

---

## 7. 前端架構

| 元件 | 功能 |
|------|------|
| `intelligence-workspace.tsx` | 狀態中心 |
| `chat-input-bar.tsx` | 幣種選擇器 + 問題輸入 |
| `chat-thread.tsx` | 報告渲染（claims + FigureGallery + enhanced report） |
| `right-panel.tsx` | ReAct Trace 即時面板 |
| `top-bar.tsx` | 即時行情 marquee (Binance WebSocket) |

### 前後端通訊

```
POST /api/v1/analyse {asset, question} → 202 {run_id, stream_url}
GET  /api/v1/stream_trace?run_id=X → SSE (event: trace/complete/error)
```

SSE 每 15 秒送心跳（`: heartbeat\n\n`）防止 proxy timeout。

---

## 8. 確定性檢核清單（不依賴 LLM 的程式碼驗證）

| 檢核 | 位置 | 功能 |
|------|------|------|
| `gate_asset` | tools.py | 幣種白名單 |
| `verify` (claim_ledger) | claim_ledger.py | 引用是否存在 |
| `enforce_paired_disclosure` | review.py | SMA60→必帶SMA200; FEDFUNDS→必帶DGS10 |
| `enforce_indicator_citations` | indicator_guard.py | 掃描幻覺指標數字 |
| `detect_core_data_demands` | question.py | 關鍵字偵測不可用資料類型 |
| `build_report_limitations` | limitations.py | 組裝限制聲明 |
| `apply_contested_penalty` | tools.py | 爭議比例修正 |
| `dedup (assign_event_keys)` | dedup.py | 同事件跨源歸併 |

---

## 9. 核心資料缺失偵測（確定性關鍵字比對）

系統不具備但可偵測的資料類型：

| 資料類型 | 關鍵字觸發 | 可用性 | 替代方案 |
|---------|-----------|--------|---------|
| 交易所儲備量 | "交易所儲備", "exchange reserve", "淨流入" | UNAVAILABLE | OI + 資金費率間接推測 |
| 巨鯨轉移 | "巨鯨", "whale", "大額轉帳" | UNAVAILABLE | 盤口深度 + OI 變化 |
| 清算圖譜 | "清算", "liquidation", "爆倉" | UNAVAILABLE | OI 變化 + 資金費率 |
| 鏈上活躍度 | "活躍地址", "on-chain activity" | UNAVAILABLE | TVL (DefiLlama) |
| DEX 交易量 | "dex volume", "去中心化交易" | UNAVAILABLE | 無替代 |
| Gas 費用 | "gas fee", "gas 費" | UNAVAILABLE | 無替代 |
| 質押收益 | "質押", "staking" | UNAVAILABLE | 無替代 |
| 美股指數 | "美股", "S&P 500", "納斯達克" | PARTIAL | FRED 宏觀間接 |
| 黃金價格 | "黃金", "gold" | PARTIAL | 美元指數反推 |
| 外匯匯率 | "匯率", "日圓", "歐元" | PARTIAL | FRED 美元指數 |

---

## 10. 同事件歸併規則

- 多家媒體報導同一事件時，只計為**一個**獨立證據
- 歸併依據：標題相似度 + 時間窗口 + `event_key` 哈希
- 保留所有來源片段（可追溯），但信心度只算一個

---

## 11. 環境需求

| 項目 | 規格 |
|------|------|
| Python | 3.11+ |
| Node.js | 18+ |
| AWS CLI | v2.36+ |
| Bedrock API Key | 必填 |
| FRED API Key | 建議（免費申請） |
| CoinGecko API Key | 選填（避免公共限流） |
| PostgreSQL + pgvector | 選填（無則降級 InMemory） |

---

## 12. 紅隊對抗分析（已知弱點與風險）

### 12.1 資料層風險

| 風險 | 描述 | 嚴重度 | 緩解 |
|------|------|--------|------|
| **Binance API 單點故障** | 技術面 + 籌碼面 + 圖表都依賴 Binance | 🔴 高 | `candlestick_builder` 有異常處理；技術面可退回 dataset |
| **RSS 被封鎖** | CoinDesk 已有 308 redirect 問題 | 🟡 中 | `follow_redirects=True`；多源 fallback |
| **FRED 數據延遲** | 經濟數據月更，可能過期 2-4 週 | 🟡 中 | freshness 會自動降分 |
| **CoinGecko 限流** | 公共 API 限 10-30 req/min | 🟡 中 | 有 API key 選項 |
| **Fear & Greed 單一來源** | alternative.me 是唯一供應者 | 🟡 中 | 新聞情緒作為獨立交叉驗證 |

### 12.2 推論層風險

| 風險 | 描述 | 嚴重度 | 緩解 |
|------|------|--------|------|
| **Claude 幻覺指標** | 模型可能自行算 MACD/KD 而非引用 | 🔴 高 | `enforce_indicator_citations` + prompt 禁令 |
| **JSON 格式不穩定** | synthesise 回應可能被截斷或格式錯 | 🔴 高 | maxTokens 8192 + 多種 JSON fallback 解析 |
| **過度自信** | 模型傾向給出確定性結論 | 🟡 中 | 五維公式 + contested penalty + 配對揭露 |
| **情緒標註全為 0** | label() 偶爾回傳全零 | 🟡 中 | LexiconLabeller fallback（字典式） |
| **Bedrock timeout** | 大 prompt (44 項證據) 可能超時 | 🟡 中 | 180s timeout + 2 次 retry |

### 12.3 呈現層風險

| 風險 | 描述 | 嚴重度 | 緩解 |
|------|------|--------|------|
| **SSE 連線中斷** | 前端 undici 的 300s body timeout | 🔴 高 | 改用 Node.js 原生 http + 後端 15s 心跳 |
| **重複圖表渲染** | 同 evidence_id 可能出現多次 | 🟡 中 | FigureGallery 去重 |
| **報告過長** | 44 項證據 + 45 則判斷 → 報告極長 | 🟡 中 | 折疊區 UI |

### 12.4 安全風險

| 風險 | 描述 | 嚴重度 | 緩解 |
|------|------|--------|------|
| **API Key 外洩** | .env 可能被 commit | 🔴 高 | .gitignore 排除；.env.example 無真值 |
| **模型注入** | 使用者問題可能含 prompt injection | 🟡 中 | `sanitizer.py` 清洗；閘門先於推論 |
| **無投資建議免責** | 報告可能被誤讀為買賣建議 | 🟡 中 | prompt 禁止保證式預測；limitations 聲明 |

### 12.5 目前未覆蓋但可能被評審問的

| 缺口 | 風險 | 建議 |
|------|------|------|
| 無回測準確度驗證 | 不知道系統歷史判斷的準確率 | 需要做 backtest evaluation |
| 無 A/B 測試 Gemini vs Claude | 不確定哪個模型品質更好 | 需要 eval harness |
| 無成本追蹤 | Bedrock 是按量付費，不知道每次分析花多少 | 加 usage metrics |
| 無鏈上資料 | 交易所儲備/巨鯨轉移完全靠新聞間接推測 | 接入 Glassnode/CryptoQuant（付費） |
| 分析時間 ~7 分鐘 | 使用者等待久 | 可用 streaming progressive rendering |

---

## 13. 檔案對照表

| 檔案 | 職責 |
|------|------|
| `src/hoyabit_agent/domain.py` | 所有領域型別 |
| `src/hoyabit_agent/seams.py` | Protocol 定義 + ToolSpec |
| `src/hoyabit_agent/run.py` | 主流程 analyse() |
| `src/hoyabit_agent/tools.py` | 純計算函數工具 |
| `src/hoyabit_agent/indicators.py` | 技術指標公式 |
| `src/hoyabit_agent/models/bedrock.py` | Bedrock Claude 實作 |
| `src/hoyabit_agent/models/prompts.py` | 提示詞模板 |
| `src/hoyabit_agent/sources/binance.py` | Binance 資料源 |
| `src/hoyabit_agent/sources/news.py` | 新聞 RSS |
| `src/hoyabit_agent/sources/fred.py` | FRED 宏觀 |
| `src/hoyabit_agent/sources/coingecko.py` | CoinGecko |
| `src/hoyabit_agent/sources/defillama.py` | DefiLlama TVL |
| `src/hoyabit_agent/sources/fear_greed.py` | 恐慌貪婪指數 |
| `src/hoyabit_agent/claim_ledger.py` | 引用驗證 |
| `src/hoyabit_agent/limitations.py` | 限制聲明組裝 |
| `src/hoyabit_agent/indicator_guard.py` | 幻覺掃描 |
| `src/hoyabit_agent/question.py` | 題型分類 + 核心資料偵測 |
| `src/hoyabit_agent/runtime_events.py` | SSE 事件廣播 |
| `frontend/src/components/chat-thread.tsx` | 報告渲染 |
| `frontend/src/app/api/v1/stream_trace/route.ts` | SSE proxy |

---

*本文件供外部 AI 審核用。如需更深入的某一層細節，可指定章節編號要求展開。*
