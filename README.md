<div align="center">

# 🔍 HoyaBit Crypto Evidence Agent

### 每一句判斷都能追回原始出處的加密貨幣分析 Agent

市面上的 AI 分析工具給你一段沒有來源的文字。<br/>
這個系統給你一份**逐句可溯源**的分析，以及它**是怎麼想出來的完整推論軌跡**。

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![uv](https://img.shields.io/badge/deps-uv-de5fe9)](https://docs.astral.sh/uv/)
[![Type Checked](https://img.shields.io/badge/mypy-strict-2A6DB2)](pyproject.toml)
[![Lint](https://img.shields.io/badge/lint-ruff-D7FF64?logo=ruff&logoColor=black)](pyproject.toml)
[![Tests](https://img.shields.io/badge/tests-631%20passing-brightgreen)](tests)
[![Next.js](https://img.shields.io/badge/frontend-Next.js%2015-black?logo=next.js)](frontend)
[![License](https://img.shields.io/badge/license-unspecified-lightgrey)](#-授權)

[核心特色](#-核心特色) ·
[系統架構](#-系統架構) ·
[快速開始](#-快速開始) ·
[使用範例](#-使用範例) ·
[技術棧](#-技術棧) ·
[專案結構](#-專案結構)

</div>

---

## 這是什麼

**HoyaBit Crypto Evidence Agent** 是一個 [ReAct](https://arxiv.org/abs/2210.03629) 架構的證據推理 Agent，
在有限時間預算內從多個異質來源（交易所行情、衍生品數據、總經指標、鏈上數據、新聞情緒）蒐集原始資料，
推論出一份**每句判斷都掛載證據識別碼**的加密貨幣分析報告，支援 BTC、ETH、SOL、BNB、XRP 五種資產。

這不是「再做一個投顧 ChatBot」。差異在三個地方：

1. **每一句判斷強制掛載證據識別碼** —— 追不回來源的句子在報告組裝階段就被系統丟棄，永遠進不了最終報告。
2. **推論軌跡是交付物，不是除錯副產品** —— 你能看到模型在哪一步發現證據不足、決定回頭補抓、
   以及那個決定如何改變了最終結論，並以 SSE 即時串流到前端。
3. **信心度衡量的是證據之間的分歧程度，不是模型自己說它有幾成把握** —— 技術面看空、社群看多時，
   系統會明說「這裡有分歧」，而不是硬給一個漂亮結論。

> ⚠️ 本專案輸出**不構成投資建議**。系統刻意避開「建議」「訊號」等帶投顧意涵的詞，只描述證據與方向。

---

## ✨ 核心特色

### 🔗 逐句可溯源的輸出契約
- 每個保留下來的判斷（Claim）必須引用至少一個證據識別碼（Evidence ID）。
- 未掛載證據的句子在組裝階段直接丟棄——但仍完整記錄在推論軌跡中，可稽核「檢核確實有在運作」。
- 每個證據都可回溯到來源 URL／檔案、取得時間、原文片段位置。

### 🧠 分層推理：固定骨架保證收斂，迴圈內才是真推理
- 外層是固定狀態圖（規劃 → 蒐集 → 缺口檢查 → 組裝），由 [LangGraph](https://github.com/langchain-ai/langgraph) 管理狀態與 checkpoint，**保證一定收斂、不會卡死**。
- 蒐集迴圈內部是真正的 ReAct：呼叫哪些工具、要不要再挖一輪，全由模型依據**當前的證據缺口**動態決定（最多 6 輪）。
- 15 分鐘壁鐘預算是上限不是目標——預算耗盡時走「以現有證據組裝」路徑，**永不回傳逾時錯誤**。

### 📊 四維證據面 + 分歧驅動的信心度
| 證據面 | 內容 |
|---|---|
| **技術面** Technical | 價量時間序列計算出的數值（均線、RSI、MACD、成交量變化）|
| **籌碼面** Positioning | 資金與持倉分布（資金費率、未平倉合約、多空比、交易所淨流入流出）|
| **基本面** Fundamental | 資產本身或網路事實（升級、解鎖、監管事件、TVL、鏈上活動）|
| **情緒面** Sentiment | 從新聞原文推導的傾向分數，永遠附帶產生它的來源片段 |

信心度 = 四個證據面彼此一致的程度，而非模型的自我評估。四面一致 → 高；技術面看空但情緒面看多 → 低。
「證據不足」與「低信心度」是明確區分的兩種狀態——只蒐到一個面時，信心度是「無法計算」，不是「高」。

### 🚧 幣種閘門：白名單而非黑名單
在推理**開始之前**執行的硬性程式檢查（不是交給模型的 prompt 指示）：請求資產不在 BTC/ETH/SOL/BNB/XRP 集合內
一律短路拒絕，不消耗任何時間或 token 預算。

### 🌐 多來源證據整合
同時打 7 類外部資料源產出 15-20+ 項證據：

`Binance 現貨/衍生品` · `CoinGecko` · `FRED 總經（利率/M2/CPI）` · `DeFiLlama TVL` · `Fear & Greed Index` · `新聞 RSS (CoinTelegraph/CoinDesk)` · `Blockchair 鏈上` · `本所聚合指標（介面預留）`

### 🤖 多模型供應者，可插拔且具備自動容錯
- **AWS Bedrock**（Claude Sonnet，推薦——按量計費無 RPM 限制）
- **Google Gemini**（免費層，雙 Key 自動輪替以翻倍 RPM）
- **Groq**（備援層）

模型供應者藏在同一個介面後（[`seams.py`](src/hoyabit_agent/seams.py)），`ResilientModelAdapter` 在主要供應者失敗時自動切換備援，不需要更動任何推理邏輯。

### ⏱️ 回測與即時雙模式，同一條 pipeline
`as_of_date`（分析截止日）是整個系統的總開關：截止日早於現在自動推導為**歷史回測模式**（禁用 live 工具，防止偷看未來），
否則為**即時模式**。行為分歧一律由這個值推導，不靠散落各處的 `is_live` 布林旗標。

### 📈 推論軌跡即時視覺化
- SSE 即時串流（`/api/v1/stream_trace`）到 Next.js 前端，時間軸與樹狀兩種視圖。
- 每個節點顯示：動作理由、輸入、產出的證據、缺口變化。
- 證據 → 判斷的連線可視化，點擊任一證據可展開原始片段、出處 URL、擷取時間。
- 顯示被引用檢核丟棄的判斷及丟棄原因。

### 📄 可交付產物：報告 / 證據清單 / 執行日誌 / PDF
每次分析回合輸出 `final_report.md`、`evidence_list.json`、`execution_log.json`，
以及內嵌 SVG K 線／RSI／走勢圖的 PDF 報告（[`pdf_generator.py`](src/hoyabit_agent/pdf_generator.py)）。

### 🔌 MCP Server：資料層可直接掛載進 Claude Desktop / Kiro
`hoyabit-mcp` 把證據源封裝為 [Model Context Protocol](https://modelcontextprotocol.io/) 工具，可獨立部署、現場即席驗證資料層是真的。

### 🗄️ 生產規格的資料層
PostgreSQL + pgvector 一套解決向量與關聯式查詢（判斷 → 證據識別碼 → 來源片段 → 出處與時間是一連串 join，
SQL 擅長、純向量庫不擅長）。可選整合 AWS Athena 資料湖、Kinesis/Firehose 即時串流、DynamoDB 快取，
未安裝 boto3 或連不到時自動退回記憶體實作，不影響核心功能。

---

## 🏗 系統架構

### 三個接縫，讓推理邏輯永遠不碰真實網路就能完整測試

```
                    ┌─────────────────────────────────────┐
   分析請求 ──────►  │        分析回合 (接縫 2)             │ ──► 分析報告
                    │  幣種閘門 → 規劃 → 蒐集迴圈           │ ──► 推論軌跡
                    │  → 缺口檢查 → 組裝 → 引用檢核         │ ──► PDF / ZIP
                    └───────┬─────────────────┬───────────┘
                            │                  │
                   ┌────────▼───────┐  ┌───────▼─────────┐
                   │ 證據源 (接縫 1) │  │ 模型供應者(接縫3) │
                   │  MCP 工具       │  │ Bedrock/Gemini/  │
                   │  Function 工具  │  │ Groq + 容錯切換  │
                   └────────────────┘  └──────────────────┘
```

所有測試打接縫 2，在接縫 1 塞假證據源、接縫 3 塞腳本假模型 → **整條 pipeline 不碰網路、不燒 token 就能完整測試**。

### 端到端資料流

```
Next.js 前端 (幣種選擇 + 問題輸入)
        │  POST /api/v1/analyse
        ▼
① 幣種閘門 ──拒絕──► 立即回絕，不進入推論
        │ 通過
        ▼
② 題型分類 + 核心資料需求偵測 (market_summary / hypothesis / technical)
        ▼
③ 預取：同時打 7 個 API（Binance／CoinGecko／FRED／DeFiLlama／
        Fear&Greed／新聞 RSS／衍生品）→ 15-20+ 項證據
        ▼
④ 缺口檢查：四面是否齊全？獨立來源 ≥2？正反方向都有？
        │ 有缺口
        ▼
⑤ ReAct 蒐集迴圈（最多 6 輪，SSE 即時推送軌跡節點）
        │  LLM Plan → 並行執行工具 → 缺口再檢查 → (缺口關閉 or 預算用罄)跳出
        ▼
⑥ 組裝：逐句引用檢核 → 信心度計算（跨面一致性）→ 反方論點 → 限制聲明
        ▼
最終報告 + 推論軌跡 + 證據清單 + PDF/ZIP 匯出
```

完整版（含每一步的輸入輸出範例）見 [docs/SYSTEM_ARCHITECTURE_DIAGRAM.md](docs/SYSTEM_ARCHITECTURE_DIAGRAM.md)。

---

## 🚀 快速開始

### 前置需求

| 需求 | 版本 | 是否必要 |
|---|---|---|
| Python | 3.11 – 3.12 | 必要 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 最新版 | 必要（套件與虛擬環境管理）|
| Node.js | 20+ | 需要前端介面時 |
| PostgreSQL 16+（含 pgvector） | — | 需要持久化存證 / RAG 檢索時；純 CLI demo 或 `dev_server.py` 不需要 |
| Bedrock 或 Gemini API Key | — | 必要（擇一，見下方設定）|

### 1. 取得程式碼

```bash
git clone https://github.com/Alan52254/crypto-evidence-agent.git
cd crypto-evidence-agent
```

### 2. 安裝後端依賴

```bash
uv sync --extra dev            # 基本開發環境（含 pytest/mypy/ruff）
uv sync --extra dev --extra export --extra vision   # 加上 PDF 匯出與圖表視覺辨識
```

### 3. 設定環境變數

```powershell
Copy-Item .env.example .env
```

編輯 `.env`，**擇一**填入模型金鑰（真實金鑰只放在被 `.gitignore` 排除的 `.env`，不會進版控）：

```dotenv
# 選項 A：AWS Bedrock Claude（推薦，按量計費無 RPM 限制）
MODEL_PROVIDER=bedrock
BEDROCK_API_KEY=your-bedrock-api-key
BEDROCK_REGION=us-east-1
BEDROCK_MODEL=us.anthropic.claude-sonnet-4-6

# 選項 B：Google Gemini（免費層）
MODEL_PROVIDER=gemini
GEMINI_API_KEY=your-gemini-api-key
```

其餘資料源（FRED、CoinGecko、Athena、Kinesis、DynamoDB）皆為可選，缺少時系統自動降級並在報告中明確揭露限制，不會讓整個流程失敗。

### 4. 啟動後端（免 Postgres，記憶體 store）

```powershell
.venv\Scripts\python.exe dev_server.py
```

啟動後 API 服務於 `http://127.0.0.1:8000`。

<details>
<summary>需要持久化 / RAG 檢索？啟動 Postgres 版本</summary>

```bash
# 啟動含 pgvector 的 Postgres 後：
uv run hoyabit-ingest              # 匯入主辦方 OHLCV 資料集（可選）
uv run hoyabit-web --host 127.0.0.1 --port 8000
```

資料庫連線字串由 `HOYABIT_DATABASE_URL` 環境變數指定，預設 `postgresql://postgres:hoyabit@localhost:5433/hoyabit`。

</details>

### 5. 啟動前端（可選，Next.js 智慧終端機介面）

```bash
cd frontend
npm install
npm run dev
```

瀏覽 `http://localhost:3000`，選擇資產、輸入問題，即時觀看推論軌跡串流與報告產出。

---

## 🧪 使用範例

### CLI：跑一次分析並產出可交付物

```powershell
uv run python -m hoyabit_agent BTC --live --save `
  --question "市場上認為 BTC 短期盤整，請驗證支持與反對證據" `
  --output-dir submissions
```

不加 `--live` 時預設跑離線假證據源（快、可重現、適合先驗證流程），分析回合本身一行都不用改——這正是「接縫」設計的意義。

每次 live 回合輸出至 `submissions/`：

| 檔案 | 內容 |
|---|---|
| `final_report.md` | 市場判斷、關鍵依據、限制與引用 |
| `evidence_list.json` | 來源、URL、取得時間、引用區間、來源層級與對應判斷 |
| `execution_log.json` | 時間序列、工具呼叫、資料取得、缺口與分析流程 |

### 推論軌跡可視化介面

```bash
uv run hoyabit-web --host 127.0.0.1 --port 8000
```

### MCP Server（掛載進 Claude Desktop / Kiro）

```bash
uv run hoyabit-mcp
```

### 評估基準

```bash
uv run hoyabit-eval
```

---

## 🧰 技術棧

| 分類 | 選型 |
|---|---|
| **推理編排** | [LangGraph](https://github.com/langchain-ai/langgraph) `1.2.9`（狀態機/checkpoint）+ [LangChain](https://github.com/langchain-ai/langchain) `1.3.14`（模型/工具抽象）|
| **模型供應者** | AWS Bedrock（Claude）／Google Gemini／Groq，經統一介面 + 自動容錯切換 |
| **RAG / 向量檢索** | [llama-index-core](https://github.com/run-llama/llama_index) + PostgreSQL `pgvector` |
| **工具協定** | [Model Context Protocol](https://modelcontextprotocol.io/) (`mcp==1.28.1`) |
| **後端服務** | [Starlette](https://www.starlette.io/) + `uvicorn`，SSE 即時串流 |
| **前端** | Next.js 15 + React 18 + Tailwind CSS + Radix UI |
| **PDF / 圖表** | ReportLab + svglib（SVG K 線圖內嵌）、Playwright（圖表視覺辨識）|
| **雲端整合（可選）** | AWS Athena（資料湖查詢）、Kinesis/Firehose（即時串流 ingestion）、DynamoDB（快取）|
| **型別與品質** | mypy `strict` 全專案、ruff（`E`/`F`/`I`/`UP`/`B`）、pytest + pytest-asyncio |

---

## 🗂 專案結構

```
crypto-evidence-agent/
├── src/hoyabit_agent/
│   ├── run.py                # 分析回合主流程（幣種閘門→規劃→蒐集→組裝）
│   ├── domain.py              # 核心領域模型（Evidence／Claim／Trace…）
│   ├── gaps.py                 # 證據缺口計算
│   ├── claim_ledger.py         # 引用檢核 / 判斷帳本
│   ├── indicators.py           # 確定性技術指標計算（Function 工具）
│   ├── mcp_server.py           # MCP 工具封裝
│   ├── pdf_generator.py        # PDF 報告產出（含內嵌 SVG 圖表）
│   ├── models/                 # 模型供應者：bedrock / gemini / groq + 容錯層
│   ├── sources/                # 證據源：binance / coingecko / fred / defillama / news…
│   ├── ingest/                 # 背景 ingestion、資料集匯入、Postgres/記憶體 store
│   ├── storage/                # Postgres + pgvector schema、DynamoDB 快取
│   └── viz/                    # 推論軌跡 web server（SSE、REST API）
├── frontend/                   # Next.js 智慧終端機介面
│   └── src/{app,components}/   # API routes、幣種面板、K 線圖、聊天式問答
├── tests/                      # 631+ 測試，pipeline 全程無 mock 依賴（假源/假模型）
├── docs/
│   ├── adr/                    # 架構決策紀錄（信心度定義、Gemini 邊界…）
│   ├── spec/                   # 完整規格與遷移紀錄
│   ├── design/                 # 模組形狀與接縫設計
│   └── SYSTEM_ARCHITECTURE_DIAGRAM.md
├── CONTEXT.md                  # 領域術語表（Ubiquitous Language）
└── PROPOSAL.md                 # 原始黑客松提案（架構取捨與理由）
```

---

## ✅ 測試與品質保證

```powershell
uv run --extra dev pytest        # 631+ 測試，pipeline 層無需真實網路或 API Key
uv run --extra dev mypy src       # strict 模式，全 src 覆蓋
uv run --extra dev ruff check src tests
```

推理邏輯與外部世界之間只有兩個接縫（證據源、模型供應者），測試時兩者皆可替換為假實作——
所有分析回合的核心邏輯測試**不打真實網路、不燒 token**。少數打真實外部 API 的契約測試以 `@pytest.mark.contract` 標記，不進 CI 主流程。

---

## 🗺 Roadmap

- [x] 單幣種端到端分析 pipeline（幣種閘門 → ReAct 蒐集 → 組裝 → 引用檢核）
- [x] 推論軌跡即時視覺化（SSE + Next.js 前端）
- [x] PDF 報告匯出（內嵌 SVG 圖表）
- [x] MCP Server 封裝
- [x] 多模型供應者容錯切換（Bedrock / Gemini / Groq）
- [x] 回測 / 即時雙模式（`as_of_date` 總開關）
- [ ] 跨幣種比較層（架構已預留：單幣分析為純函數、輸出結構化物件，fan-out 即可）
- [ ] Scope swap 的 deterministic 偵測（目前依賴模型自身判斷力，見 [CONTEXT.md](CONTEXT.md#已知架構風險2026-08-bedrock-遷移後)）
- [ ] 本所聚合指標真實串接（介面已預留）

---

## 🤝 貢獻指南

歡迎 Issue 與 PR！在開始之前：

1. 閱讀 [CONTEXT.md](CONTEXT.md) 了解本專案的領域術語（Evidence／Facet／Claim／Trace 等詞彙有精確定義，避免用同義詞混用）。
2. 閱讀相關 [docs/adr/](docs/adr/) 了解既有架構決策背後的理由，避免重新踩過已被否決的方案。
3. 送 PR 前確保 `pytest` / `mypy src` / `ruff check` 全過。
4. Issue 以 GitHub Issues 追蹤，標籤慣例見 [docs/agents/triage-labels.md](docs/agents/triage-labels.md)。

---

## ⚠️ 免責聲明

本專案輸出的分析報告**不構成投資建議**。所有判斷皆基於可回溯的公開資料與確定性計算，
但加密貨幣市場高度波動，過去或即時證據不保證未來結果。請自行判斷風險並對投資決策負全部責任。

---

## 📄 授權

本專案目前尚未附加開源授權條款（LICENSE）。若需引用、修改或再散布，請先與作者確認授權方式。

---

## 🙏 致謝

本專案為 **2026 雲湧智生：臺灣生成式 AI 應用黑客松競賽** 參賽作品，以 HoyaBit 命題資料集為基礎開發。

<div align="center">

如果這個專案對你有幫助，歡迎點個 ⭐️ Star！

</div>
