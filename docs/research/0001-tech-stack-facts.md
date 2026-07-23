# 研究 0001 — 技術選型事實查證

- **日期**：2026-07-23
- **方法**：`/research`，以官方來源（PyPI JSON API、官方文件、官方 blog）為主
- **目的**：把 ADR 0001 留下的「事實類未決項」查清楚，供 `/to-spec` 定版本號

---

## 摘要：兩個會打亂時程的發現

1. **Reddit API 自助註冊已關閉，新的 OAuth client 需人工審核，週期 2-4 週。**
   專案總長就是 4 週 → 若把 Reddit 當作社群情緒的主要來源，**極可能整個專案期間都拿不到憑證**。
   必須立刻申請（今天），並且**不能把它列為關鍵路徑**。
2. **MCP 規格正在改版：2026-07-28 版本在 5 天後發布**，是 launch 以來最大改版
   （stateless core、Extensions framework、Tasks、MCP Apps、authorization hardening）。
   Python SDK v2 目前只有 alpha/beta。→ **必須釘死 1.28.1，不要碰 2.0.0aN/bN**。

---

## 1. AWS Kiro — 定位澄清（重要）

**Kiro 是 spec-driven 的 agentic IDE（開發工具），取代原本的 Amazon Q，不是一個可以呼叫的 LLM。**
它不能當作系統的「主腦 / Main Router & Planner」— 那是 runtime 的角色，Kiro 不提供 inference API。

- 計費單位是 **credits**，代表 AI 任務的運算量。
- 免費層每月 50 credits；Pro $20/月（1,000 credits）、Pro+ $40、Pro Max $100、Power $200/月（10,000 credits）。
- 超額 pay-as-you-go **$0.04 / credit**；加購 credits 自購買日起 12 個月後到期。

→ 競賽給的 **2000 credits 約當 $80 的開發額度**，用途是「用 Kiro 來寫這個專案」，
與 runtime 選哪個模型完全無關。加分要靠**開發過程用了 Kiro 的 spec / hooks / agents**，
所以應該在提案中呈現 Kiro 的 spec 檔與 hook 設定，而不是宣稱「主腦用 Kiro」。

來源：[Kiro Pricing](https://kiro.dev/pricing/)、[Kiro FAQ](https://kiro.dev/faq/)、
[AWS Kiro Developer Guide 2026](https://www.developersdigest.tech/blog/aws-kiro-developer-guide-2026)、
[The Register — Kiro pricing](https://www.theregister.com/2025/08/18/aws_updated_kiro_pricing/)

---

## 2. 框架版本（釘死用）

| 套件 | 版本 | 備註 |
|---|---|---|
| `langgraph` | **1.2.9**（2026-07-10） | Python ≥3.10；相依 `langchain-core >=1.4.7,<2` |
| `langchain` | **1.3.14** | Python >=3.10,<4.0 |
| `llama-index-core` | **0.14.23** | Python >=3.10,<4.0 |
| `mcp`（Python SDK） | **1.28.1**（2026-06-26） | 最新穩定版；v2 僅 `2.0.0a1`/`2.0.0b1` 預發布 |

- LangGraph 與 LangChain 都已達 1.0 里程碑並宣稱向後相容，1.x 線是安全的。
- **LangGraph 與 LangChain 不是二選一**：LangGraph 是低階的有狀態編排框架（durable execution、
  streaming、human-in-the-loop、persistence），LangChain 提供上層模型/工具抽象。
  本專案的用法是 **LangGraph 負責 ReAct 狀態機與 checkpoint，LangChain 只用其 model/tool 介面**。
- **MCP 版本風險**：2026-07-28 規格 5 天後定案，SDK v2 是為它重寫的。4 週專案應 pin `mcp==1.28.1`，
  在 `pyproject.toml` 寫死上界，避免中途被拉進 v2 的破壞性變更。

來源：[langgraph PyPI](https://pypi.org/project/langgraph/)、[langchain PyPI](https://pypi.org/project/langchain/)、
[llama-index-core PyPI](https://pypi.org/project/llama-index-core/)、
[MCP python-sdk releases](https://github.com/modelcontextprotocol/python-sdk/releases)、
[MCP 2026-07-28 Release Candidate](https://blog.modelcontextprotocol.io/posts/2026-07-28-release-candidate/)、
[Beta SDKs for 2026-07-28 spec](https://blog.modelcontextprotocol.io/posts/sdk-betas-2026-07-28/)、
[LangChain & LangGraph 1.0](https://www.langchain.com/blog/langchain-langgraph-1dot0)

---

## 3. 資料源（依 ADR 決策 #5：只用免費源）

### 3.1 市場／技術面

| 來源 | 額度 | 需金鑰 | 評估 |
|---|---|---|---|
| **Binance 公開 REST**（klines、depth、ticker） | 以 **IP** 計 weight，非以金鑰 | **否** | **首選**。無金鑰、無註冊、資料粒度足夠做技術面。回應標頭 `X-MBX-USED-WEIGHT-*` 可即時監控用量 |
| **CoinGecko Demo** | **100 calls/min、10,000 calls/月** | 是（免費申請） | 次選。月額度 10k 對 5 幣種夠用，但要注意 ingestion 輪詢會很快吃掉 |
| **DIA** | 免費、3,000+ 代幣、100+ 交易所 | **否** | 備援價格源，可作交叉驗證 |

**注意**：月額度 10,000 除以 30 天 ≈ 每天 333 次。若背景 ingestion 每 5 分鐘輪詢一次就是每天 288 次，
幾乎用光 → **CoinGecko 不適合當輪詢主力，應以 Binance 為輪詢來源、CoinGecko 僅作補充欄位。**

### 3.2 籌碼面 / 衍生品

- **CoinGlass**：提供交易所錢包淨流入流出（netflow）、資金費率總覽與歷史、未平倉合約（OI）、
  清算數據。有免費層，需查證當期額度。
- **CoinAPI**：歷史資金費率（Binance/Bybit/OKX/Deribit），需免費 developer key，額度有限。

→ 這一層**符合命題「不能直接調用已有結論的 API」**：拿到的是原始數值序列，結論由我們自己算。

### 3.3 新聞

- **Cointelegraph RSS**：`https://cointelegraph.com/rss` — 免費、無金鑰、無額度限制。
- **CoinDesk RSS**：官方有提供 feed。
- → RSS 是本專案性價比最高的一層：零成本、零額度、零註冊，且天然帶時間戳與原文連結（利於溯源）。

### 3.4 社群情緒 ⚠️ 時程風險

- **Reddit Data API**：非商業用途免費，**經 OAuth 每個 client 100 QPM**；未經 OAuth 僅 **10 QPM**。
  額度以 10 分鐘視窗平均，允許突發。
- ⚠️ **自助註冊已關閉，新 OAuth token 需人工核准，時程 2-4 週。**
- 商業用途 **$0.24 / 1,000 calls**，需人工審核的合約。
- **注意合規**：本專案屬交易所商業情境，嚴格說不適用「非商業免費層」。

→ **行動項（今天就要做）**：立刻送出 Reddit OAuth 申請。同時**設計上不得依賴它** —
情緒層的主力改為「新聞 RSS 全文 + WebSearch 結果」，Reddit 作為 adapter 後的可選來源，
核准下來就插上，沒下來也不影響 Demo。

來源：[Binance REST API limits](https://developers.binance.com/docs/binance-spot-api-docs/rest-api/limits)、
[CoinGecko public plan rate limit](https://support.coingecko.com/hc/en-us/articles/4538771776153-What-is-the-rate-limit-for-CoinGecko-API-public-plan)、
[DIA free crypto API](https://www.diadata.org/free-crypto-api/)、
[CoinGlass Crypto API](https://www.coinglass.com/CryptoApi)、
[Reddit API 2026 pricing & limits](https://www.socialcrawl.dev/blog/reddit-data-api-2026)、
[Reddit API rate limits 2026](https://www.redditapis.com/blogs/reddit-api-rate-limits-2026)、
[Cointelegraph RSS](https://newsloth.com/popular-rss-feeds/cointelegraph-rss-feeds)

---

## 4. 模型與定價（對應 ADR 決策 #6 分層策略）

### 4.1 推理層候選（每百萬 tokens，輸入/輸出）

| 模型 | 價格 | 備註 |
|---|---|---|
| Claude Opus 4.8 | **$5 / $25** | |
| Claude Sonnet 5 | **$3 / $15** | |
| Gemini 3.1 Pro | **$2 / $12** | |
| Gemini 3.5 Flash | $1.50 / $9 | 2026-05-19 推出 |

### 4.2 勞務層候選

| 模型 | 價格 | 備註 |
|---|---|---|
| Claude Haiku 4.5 | $1 / $5 | |
| Gemini 3 Flash | $0.50 / $3 | |
| Gemini 3.1 Flash-Lite | $0.25 / $1.50 | |
| Gemini 2.5 Flash-Lite | **$0.10 / $0.40** | 最便宜 |
| **Gemma（open weights）** | **$0 / token** | 可自架，無 per-token 成本 |

### 4.3 免費層現況（重要）

- Google AI Studio 免費層：Flash 系列約 **5-15 RPM、每日上限 1,000 次請求**。
- ⚠️ **自 2026-04-01 起，Pro 系列已從免費層移除**，只剩 Flash / Flash-Lite 免費且額度縮減。
- → **推理層無法靠免費層**，必須編列付費預算。但依命題「token 消耗不是關注重點」，
  且 5 幣種、單次分析的量級下，實際花費是個位數美元等級，非決策因素。

### 4.4 對「地端 Fine-Tuning」的評估基準（回應原企劃書第三節）

原企劃書表格的方向大致正確，但需修正兩點：

1. **「AWS Kiro, Claude 3.5, Gemini 1.5」不是有效的雲端模型清單** — Kiro 不是模型（見第 1 節），
   Claude 3.5 與 Gemini 1.5 已落後數個世代。應改為上表 4.1 的現役型號。
2. **「地端微調在格式遵循上優於雲端」在 2026 已不成立** — 現役旗艦模型都支援
   structured output / JSON schema 強制約束，格式遵循不再是微調的理由。
   微調真正還站得住的理由只剩：**資料不能出境**、**極高頻呼叫的單位成本**、**極低延遲**。

→ 本專案的建議評估基準（四項，可量化）：

| 基準 | 門檻 | 為何選它 |
|---|---|---|
| **多輪 tool calling 成功率** | ≥95%（10 步以內不迷失、不重複呼叫） | ReAct 迴圈的成敗直接由此決定 |
| **單次分析壁鐘時間** | ≤15 min（硬性，命題規定） | 決定並行度與模型延遲的可接受範圍 |
| **引用忠實度** | 抽樣 50 句判斷，掛載的證據 ID 確實支撐該句 ≥90% | 這是輸出契約的核心，也是評審看的東西 |
| **單次分析成本** | ≤ US$1 | 非決策因素，但要有數字證明可規模化 |

→ **結論不變**：勞務層可以用 Gemma 自架（真的省），但**推理層在 4 週內不做微調** —
微調無法改善的正是最關鍵的那項（多輪 tool calling 成功率）。

來源：[Claude API pricing July 2026](https://benchlm.ai/anthropic/api-pricing)、
[Anthropic API pricing 2026](https://www.cloudzero.com/blog/claude-api-pricing/)、
[Gemini API pricing July 2026](https://benchlm.ai/google/api-pricing)、
[Google Gemini API pricing 2026](https://www.opslyft.com/blog/google-gemini-api-pricing-2026)

---

## 5. 向量資料庫選型

2026 年的共識：

- **pgvector** — 已經在跑 Postgres、資料量 <5M 向量、重視維運簡單 → 就用它。
- **Qdrant** — 新專案的預設；Rust 寫成，**payload filter 先於 ANN 搜尋**（語意上正確的行為），
  小規模的額外開銷比日後被迫遷移便宜。
- **Chroma** — `pip install chromadb` 上手最快，適合原型；正式環境仍落後 Qdrant/Weaviate。

**本專案建議：pgvector。** 理由——

1. 只有 5 個幣種、4 週的 ingestion，資料量遠低於 5M 向量的分界。
2. 我們**同時需要關聯式資料**（trace 事件、證據表、來源表、去重鍵），
   用 Postgres 一套解決，省掉「兩個儲存體之間資料不一致」這個 4 週內最不划算的問題。
3. 證據溯源需要 join（結論 → 證據 ID → 原文 chunk → 來源 metadata），這正是 SQL 擅長而向量庫不擅長的。

來源：[Vector databases compared 2026](https://layerbase.com/blog/vector-databases-compared-2026)、
[Top 5 Vector Databases 2026](https://guptadeepak.com/tools/top-5-vector-databases-2026/)、
[Vector DB comparison 2026](https://jangwook.net/en/blog/en/vector-db-comparison-2026-qdrant-chroma-pgvector/)

---

## 6. 建議釘死的技術棧

```toml
# pyproject.toml — 提議版本（待 /to-spec 確認）
requires-python = ">=3.11,<3.13"

langgraph        = "==1.2.9"
langchain        = "==1.3.14"
llama-index-core = "==0.14.23"
mcp              = "==1.28.1"   # 不要升 2.x：2026-07-28 規格為破壞性改版
```

- 儲存：**PostgreSQL 16+ 搭配 pgvector 擴充**（向量 + 關聯式一套解決）
- 推理層：Gemini 3.1 Pro（$2/$12）或 Claude Sonnet 5（$3/$15），藏在 provider 介面後可切換
- 勞務層：Gemini 2.5 Flash-Lite（$0.10/$0.40）起步；若要展示技術深度再換自架 Gemma
- 資料源優先序：Binance 公開 REST（無金鑰）> 新聞 RSS（無金鑰）> CoinGecko Demo > CoinGlass > Reddit（待核准）

---

## 7. 待辦（時程敏感）

| # | 行動 | 期限 | 理由 |
|---|---|---|---|
| 1 | 送出 Reddit OAuth client 申請 | **今天** | 人工審核 2-4 週，等於整個專案期 |
| 2 | 註冊 CoinGecko Demo 金鑰 | 本週 | 即時生效，無風險 |
| 3 | 在 `pyproject.toml` 對 `mcp` 寫死上界 | 建專案時 | 2026-07-28 破壞性改版在 5 天後 |
| 4 | 查證 CoinGlass 免費層當期實際額度 | 第 1 週 | 本次未查到確切數字，是籌碼面的唯一缺口 |
