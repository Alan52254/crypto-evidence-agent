---
inclusion: auto
---

# Codebase Quick Reference

本文件是程式碼結構快速索引，幫助 Kiro 在修改時定位正確檔案。

## 目錄結構

```
src/hoyabit_agent/
├── domain.py            # 所有領域型別 (Asset, Facet, Evidence, Report, Trace...)
├── seams.py             # 三個接縫的 Protocol 定義 + ToolSpec
├── run.py               # 接縫 2: analyse() 主流程 (ReAct 蒐集迴圈)
├── tools.py             # Function 工具 (gate_asset, evidence_gap, assess_confidence, check_citations)
├── indicators.py        # 技術指標確定性計算 (RSI, EMA, Volume Z-score)
├── dedup.py             # 同事件歸併 (ADR 0002 證據獨立性)
├── arguments.py         # 工具參數驗證/降級
├── mcp_server.py        # MCP stdio server (ToolSpec → inputSchema)
├── config.py            # dotenv + async 啟動
├── artifacts.py         # AnalysisStore 實作 (PostgreSQL)
├── cli.py               # 命令列進入點
├── api_contract.py      # 後端 API 回傳格式轉換
├── charts.py            # 圖表產生
├── claim_ledger.py      # Claim 追蹤帳本
├── gaps.py              # 題型導向的缺口評估
├── question.py          # 題型分析與證據需求推導
├── reliability.py       # 資料源可靠度追蹤
├── report_enhanced.py   # 增強版報告產生
├── evidence_quality.py  # 證據品質評分
├── evaluation.py        # 評估基準
├── eval_cli.py          # 評估命令列
├── sanitizer.py         # 使用者問題清洗
│
├── models/
│   ├── gemini.py        # 接縫 3 實作: GeminiProvider (plan/synthesise/label)
│   ├── groq.py          # 備用 Groq provider
│   ├── factory.py       # 模型工廠 (依 env 選 provider)
│   ├── prompts.py       # 提示詞模板
│   ├── schemas.py       # 結構化輸出 schema
│   └── resilience.py    # 模型降級/重試邏輯
│
├── sources/
│   ├── binance.py       # BinanceSpotSource + BinanceDerivativesSource
│   ├── news.py          # NewsRssSource (CoinDesk/CoinTelegraph)
│   └── rss_extended.py  # ExtendedNewsSource + OfficialAnnouncementSource
│
├── ingest/
│   ├── runtime.py       # composition root: build_competition_sources()
│   ├── dataset.py       # CSV → 技術指標計算
│   ├── documents.py     # 指標 → 30 天窗口 MarketDocument
│   ├── embeddings.py    # Gemini Embedding API
│   ├── historical.py    # pgvector 相似度搜尋 → Evidence
│   ├── postgres_store.py # MarketDocument CRUD
│   └── cli.py           # ingest 命令列
│
├── storage/
│   └── postgres.py      # 資料庫連線 + reachable()
│
└── viz/
    └── server.py        # Starlette web server (前端 API + SSE)

frontend/src/
├── app/
│   ├── page.tsx                    # 首頁
│   ├── layout.tsx                  # 全局 layout
│   └── api/v1/
│       ├── analyse/route.ts        # POST 觸發分析
│       ├── health/route.ts         # 健康檢查
│       ├── runs/route.ts           # 列出歷史回合
│       ├── runs/[run_id]/route.ts  # 取得單一回合
│       ├── crypto-prices/route.ts  # 即時行情 proxy
│       └── stream_trace/route.ts   # SSE 軌跡串流
├── components/
│   ├── intelligence-workspace.tsx  # 狀態中心
│   ├── chat-thread.tsx             # 對話與報告渲染
│   ├── chat-input-bar.tsx          # 輸入 (asset selector + text)
│   ├── top-bar.tsx                 # 市場行情 marquee
│   ├── right-panel.tsx             # ReAct Trace 即時面板
│   └── sidebar.tsx                 # 導航
└── lib/
    ├── contracts.ts                # TypeScript 型別 (對齊 domain.py)
    └── utils.ts                    # 工具函數
```

## 核心資料流

1. 使用者 → `POST /api/v1/analyse` → Python `viz/server.py` → `run.analyse()`
2. `analyse()` 內部: 閘門 → 題型分析 → ReAct 迴圈 (plan → gather → gap_check) → synthesise → check_citations → assess_confidence → Report
3. 同時: 每個 trace node → SSE → 前端 `right-panel.tsx` 即時更新
4. 完成: `AnalysisOutcome` → `AnalysisStore.save()` → PostgreSQL

## 關鍵介面 (修改前必讀)

- **新增資料源**: 實作 `EvidenceSource` protocol (seams.py) → 加入 `ingest/runtime.py`
- **換/加 LLM**: 實作 `ModelProvider` protocol (seams.py) → 加入 `models/factory.py`
- **改信心度邏輯**: `tools.py#assess_confidence` (純函數，直接測)
- **改缺口判斷**: `tools.py#evidence_gap` + `gaps.py`
- **改報告格式**: `domain.py#Report.to_markdown` + `report_enhanced.py`
- **前後端介面**: `api_contract.py` ↔ `frontend/src/lib/contracts.ts`

## 環境需求

- Python 3.11+ (venv at `.venv/`)
- Node.js (frontend via `npm`)
- PostgreSQL + pgvector (optional, 降級設計)
- `GEMINI_API_KEY` in `.env`
- MCP server: `uv run hoyabit-mcp` (已設定在 `.kiro/settings/mcp.json`)
