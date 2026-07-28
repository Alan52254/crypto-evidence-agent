# System Architecture Design Guide

> 本文件是程式碼導讀 + 設計決策解說，幫助接手者快速理解系統如何運作。

---

## 1. 整體架構：四個接縫 (Four Seams)

```
┌──────────────────────────────────────────────────────────┐
│                    分析回合 (run.py)                        │
│  閘門 → 規劃 → 蒐集(ReAct) → 缺口檢查 → 組裝 → 報告      │
└──────────┬───────────────┬───────────────┬───────────────┘
           │               │               │
    接縫 1: EvidenceSource  接縫 3: ModelProvider  接縫 4: AnalysisStore
    (外部 I/O)             (LLM 推理)            (持久化)
           │               │               │
    ┌──────┴──────┐   ┌───┴───┐      ┌───┴────────┐
    │ Binance API │   │Gemini │      │ PostgreSQL │
    │ News RSS    │   │(plan) │      │ + pgvector │
    │ OHLCV CSV   │   │(synth)│      └────────────┘
    └─────────────┘   └───────┘
```

**為什麼這樣切？**
- 接縫 = 可替換點。測試時塞假的，正式跑時接真的
- `run.py` 不碰網路、不碰資料庫 → 測試不需要任何 mock
- 新增資料源只要實作 `EvidenceSource` protocol
- 換模型只要實作 `ModelProvider` protocol

**接縫外面還有一圈「確定性層」** —— `run.py` 的骨架呼叫這些純函式模組，
它們不押在模型回應上，見第 2.5 節：
- `question.py` 題型分類 → 決定「這一題需要什麼證據」
- `gaps.py` 缺口規則 → 決定「什麼算蒐集完成」（收斂由規則判定，不由模型說了算）
- `reliability.py` 來源分級 → 依 source_id 前綴給 HIGH/MEDIUM/LOW 權重
- `claim_ledger.py` 判斷驗證 → 撰寫與驗證分離，避免自我確認偏誤
- `as_of_date` / `AnalysisRegime` → 回測 vs 即時的實體鎖，防「偷看未來」

---

## 2. 金融分析：現在做了什麼、還缺什麼

### 已完成的分析能力

| 面向 | 資料源 | 做法 |
|------|--------|------|
| **技術面** | 競賽 OHLCV CSV + Binance Spot API | RSI/EMA/Volume 由程式確定性計算（不讓 LLM 心算） |
| **籌碼面** | Binance Derivatives API | 資金費率、未平倉合約、多空帳戶比 |
| **情緒面** | News RSS (CoinDesk/CoinTelegraph) | Gemini 批次打分 (label 方法) |
| **基本面** | 新聞中的事件（升級/監管/解鎖） | 由 label(aspect=FUNDAMENTAL) 分離出事件影響 |

### 金融分析的缺口

已解決（✅）與仍待優化（🔧）：

1. ✅ **即時 vs 歷史行情混用** — 已導入 `as_of_date` / `AnalysisRegime`（回測/即時），
   回測模式在建 registry 時就過濾掉不合規的即時來源，實體鎖防「偷看未來」（ADR 0005）。
2. 🔧 **鏈上數據缺失** — 仍無 Glassnode/DefiLlama 的 TVL、巨鯨流入、質押量。
3. 🔧 **技術指標單薄** — 仍以 RSI/EMA/Volume 為主，缺布林通道、MACD、VWAP。
4. ✅ **矛盾檢測被動** — synthesis prompt 已加入主動反方搜尋規則，且 `gaps.py`
   在方向一面倒時阻止收斂；矛盾被「揭露」而非「阻擋」。
5. 🔧 **信心度權重** — 已從單一「一致性」擴充為多維（來源可信度 via `reliability.py`、
   獨立來源數、時效、矛盾），但尚未完全對齊規格的固定百分比權重。
6. ✅ **跨幣比較** — `question.py` 已有 `COMPARATIVE_ANALYSIS` 題型；比較題不強求四面全齊，
   避免兩幣 × 四面吃光 15 分鐘預算。

### 2.5 確定性驗證層（run.py 骨架呼叫的純函式）

模型負責「規劃」與「撰寫」，但**收斂與驗證由不押在模型回應上的規則決定** ——
這是避免自我確認偏誤的核心設計。

| 模組 | 職責 | 為什麼是確定性 |
|---|---|---|
| `question.py` | 題型分類（MARKET_SUMMARY / HYPOTHESIS_VERIFICATION / COMPARATIVE）| 誤判題型會毀掉整輪蒐集，不能押在模型 |
| `gaps.py` | 題型導向的缺口判定與收斂條件 | 「什麼算蒐集完成」由規則說了算，不由模型說「我做完了」|
| `reliability.py` | 來源可信度分級（依 source_id 前綴 HIGH/MEDIUM/LOW）| 同事件轉載併為一個來源，防止灌高獨立性 |
| `claim_ledger.py` | 判斷三態驗證（supported / contested / unsupported）| **撰寫與驗證分離**；被拒絕的判斷保留為不確定性來源 |
| `as_of_date` / `AnalysisRegime` | 回測 vs 即時的實體鎖 | 唯一合法的時鐘讀取點，錨定證據新鮮度與缺口時效 |

這一層全部**無網路、無 mock 可測**，是測試金字塔的底座。

---

## 3. MCP 工具設計

### 什麼是 MCP？

MCP (Model Context Protocol) 是讓 AI 工具（Kiro、Claude Desktop）能直接呼叫你的資料源。在這個專案裡，MCP 的用途是：

**Demo 現場讓評審用自然語言即席查證資料**
- "BTC 的 RSI 是用哪段 OHLCV 算的？"
- "截至 2026-05-15 的 ETH 價格趨勢如何？"

### 一份規格，四個消費者

```python
# seams.py
@dataclass(frozen=True)
class ToolSpec:
    name: str          # "binance_spot", "crypto_news", "market_dataset_context"
    description: str   # 工具的用途描述
    parameters: JsonSchema  # JSON Schema 定義參數
```

這一份 ToolSpec 同時被：
1. **Gemini** 拿去當 `functionDeclarations` → 推理層知道可以呼叫什麼
2. **Groq/OpenAI 相容** 拿去當 `tools`（`_to_openai_tool`）→ 備援/容錯層看到同一組工具
3. **MCP Server** 拿去當 `inputSchema` → Kiro/Claude Desktop 知道工具介面
4. **run.py 執行器** 拿去當路由表 → `registry[invocation.tool].fetch(...)`

**好處**：四方永遠一致，不可能出現「模型以為有個參數」但「MCP 沒暴露」的 bug。

### 目前有哪些 MCP 工具？

以 `ingest/runtime.py` 的 `build_competition_sources()` 為權威清單：

| 工具名稱 | 資料來源 | 回傳的證據面 | 合規模式 |
|----------|----------|-------------|---------|
| `binance_spot` | Binance 現貨 API (K線+盤口) | technical + positioning | LIVE |
| `binance_derivatives` | Binance 永續合約 (費率/OI/多空比) | positioning | LIVE |
| `crypto_news` | CoinDesk/CoinTelegraph RSS | sentiment + fundamental | LIVE |
| `extended_news` | 延伸新聞來源 | sentiment + fundamental | LIVE |
| `official_announcements` | 官方公告 | fundamental | LIVE |
| `coingecko_market` | CoinGecko Demo API | technical + positioning | LIVE |
| `market_dataset_context` | 競賽 OHLCV CSV + pgvector 向量檢索 | technical | BACKTEST |

`hoyabit_flow`（本所自家聚合指標，籌碼面）已備介面 + 示意資料，
未掛進預設 live 堆疊（避免示意數字污染真實報告），未來真實 adapter 可直接替換。

> ⚠️ 工具名稱以各 `Source.spec.name` 為唯一真實來源。若這張表與程式碼不符，以程式碼為準。

### 新增一個資料源要做什麼？

```python
# 1. 建立 sources/my_source.py
class MySource:
    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(name="my_tool", description="...", parameters={...})

    async def fetch(self, asset: Asset, arguments: Arguments) -> tuple[Evidence, ...]:
        # 做 HTTP 請求，回傳 Evidence 物件
        # 失敗回傳空 tuple，不拋例外！
        ...

# 2. 在 ingest/runtime.py 的 build_competition_sources() 加進去
sources.append(MySource(client))

# 3. 完成。MCP server 和 Gemini 自動看到新工具。
```

---

## 4. 資料庫設計 (PostgreSQL + pgvector)

### 為什麼選 Postgres 不用專門的向量庫？

> 證據溯源本質上是一連串 JOIN（判斷 → 證據 ID → 來源片段 → 出處），
> 那是 SQL 擅長而向量庫不擅長的。

### Schema 結構

```
analysis_run (主表)
├── run_id (PK)
├── asset, stance, question
├── confidence_value, confidence_cause, confidence_facet_stances
└── created_at

evidence (證據)
├── run_id + evidence_id (PK)
├── facet, summary, stance_hint, event_key
└── position (排序用)

source_excerpt (來源片段)
├── id (serial PK)
├── run_id + evidence_id (FK → evidence)
├── source_id, url, retrieved_at
├── locator (在原文中的位置)
└── text (原始片段文本)

claim (判斷)
├── id (serial PK)
├── run_id (FK → analysis_run)
├── text, facet, role
├── evidence_ids (JSONB array)
├── kept (bool) ← 通過引用檢核的為 true
└── position

trace_node (推論軌跡)
├── run_id + seq (PK)
├── kind, reason
├── evidence_ids, gap_before, gap_after
├── elapsed_seconds
├── executions (JSONB) ← 工具呼叫記錄
└── gap_state (JSONB)
```

### 設計重點

1. **被丟棄的判斷也存** — `claim.kept = false`，因為「系統拒絕了什麼」本身是稽核證據
2. **evidence_id 只在 run 內唯一** — 同一則新聞在不同分析回合有不同擷取時間
3. **ON DELETE CASCADE** — 刪除 analysis_run 自動清除所有相關資料
4. **pgvector** — 用於 market_dataset_context 的向量檢索（embedding 後存在另一張表）

---

## 5. 前端框架：React + Next.js — 適合嗎？

### 短答：完全適合

| 考量 | Next.js 的優勢 |
|------|---------------|
| SSE 即時串流 | App Router 的 Route Handlers 原生支援 streaming |
| SEO 不需要 | 這是工具型 app 不是內容站，SPA 足夠 |
| 快速迭代 | React 生態系成熟，UI 庫多 |
| 部署彈性 | 可 standalone 輸出到 Docker |

### 前端程式碼結構導讀

```
intelligence-workspace.tsx  ← 狀態中心 (asset, report, events, running)
    ↓ props
├── top-bar.tsx             ← 市場行情 + 功能按鈕
├── sidebar.tsx             ← 導航 (workspace/history/library/reports/settings)
├── chat-thread.tsx         ← 對話流 (user bubble + AI bubble + report 渲染)
├── chat-input-bar.tsx      ← 輸入 (asset selector + textarea)
└── right-panel.tsx         ← ReAct Trace 即時面板
```

**資料流**：
1. 使用者輸入 → `submit()` → POST `/api/v1/analyse`
2. 後端回 `{run_id, stream_url}` → 前端開 `EventSource(stream_url)`
3. SSE `trace` 事件 → `setEvents([...events, node])` → 右側面板即時更新
4. SSE `complete` 事件 → `setReport(data)` → 主區域渲染報告

---

## 6. 競賽資料怎麼讀？每次都會讀嗎？

### 答案：不是每次都讀 CSV

資料讀取流程：

```
啟動時 (一次性)：
CSV 檔案 → 計算技術指標 → 切成 30 天窗口文件 → embedding → 存入 PostgreSQL

分析時 (每次)：
問題 → embedding → pgvector 相似度搜尋 → 取出最相關的文件 → 作為 Evidence
```

### 具體程式碼路徑

1. **`ingest/dataset.py`** — 讀取 CSV，計算指標（RSI/EMA/Volume Z-score）
2. **`ingest/documents.py`** — 把指標資料切成 30 天窗口的 `MarketDocument`
3. **`ingest/embeddings.py`** — 用 Gemini Embedding API 向量化
4. **`ingest/postgres_store.py`** — 存入 PostgreSQL (帶 vector column)
5. **`ingest/historical.py`** — 查詢時：問題 → embedding → pgvector 搜尋 → Evidence

**重點**：競賽 CSV 是「歷史背景」，不是「即時行情」。任何「當前市場」結論都必須來自 Binance API 的新鮮數據。

### 不讀資料庫也能跑嗎？

可以！如果 PostgreSQL 不可達：
- `build_market_evidence_source()` 回傳 None
- `build_competition_sources()` 仍然有 Binance + News 三個來源
- Agent 少了歷史技術面背景，但不會崩潰（降級設計）

---

## 7. 程式碼品質提升建議

### 如何讀懂 run.py (最核心的 200 行)

```python
async def analyse(request, sources, model, *, on_trace=None):
    # 1. 幣種閘門 — 不在白名單直接拒絕
    asset = gate_asset(request.asset)

    # 2. ReAct 蒐集迴圈 — 最多 6 次
    for _ in range(max_iterations):
        gap = evidence_gap(gathered)    # 哪些面還缺證據？
        if not gap: break               # 四面都夠了 → 結束

        decision = await model.plan(context, tools)  # 問 LLM：下一步呼叫什麼？
        results = await asyncio.gather(...)          # 平行呼叫多個資料源
        gathered = merge_independent_evidence(...)    # 歸併 + 去重

    # 3. 組裝階段
    drafts = await model.synthesise(asset, gathered, question)  # LLM 從證據寫判斷
    kept, dropped = check_citations(drafts, gathered)           # 引用檢核過濾
    confidence = assess_confidence(gathered)                     # 計算信心度
    return AnalysisOutcome(report=Report(...), trace=...)
```

### 測試怎麼寫？

```python
# 1. Function 工具 → 直接測，不需要 mock
def test_neutral_facets_do_not_vote():
    evidence = (make_evidence(Facet.TECHNICAL, 0.0), ...)
    result = assess_confidence(evidence)
    assert result.value == ...

# 2. 分析回合 → 塞假 source + 假 model
async def test_agent_gathers_until_gap_is_closed():
    sources = [FakeSource(produces=[evidence_a, evidence_b])]
    model = ScriptedModel(plan_responses=[...], synth_response=[...])
    outcome = await analyse(request, sources, model)
    assert outcome.report is not None
```

---

## 8. 架構決策總結

| 決策 | 選擇 | 替代方案 | 理由 |
|------|------|----------|------|
| Agent 框架 | 自己寫 run.py | LangGraph | 200 行可完全掌控，不受框架版本波動 |
| LLM | Gemini 3.6 Flash | GPT-4 / Claude | 免費層 1M context + 原生 function calling |
| 資料庫 | PostgreSQL + pgvector | Pinecone + 另一個 SQL | 一套解決向量 + 關聯式 |
| 前端 | Next.js + React | Vue / Svelte | SSE 支援好 + 生態最大 |
| API | Starlette | FastAPI | 更輕量，夠用 |
| MCP | 自己寫 server | 框架模板 | 與 ToolSpec 共用，保證三方一致 |
