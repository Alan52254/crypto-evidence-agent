# crypto-evidence-agent

> 市面上的 AI 分析工具給你一段沒有來源的文字。
> 這個 Agent 給你一份**每句話都能追回原始出處**的分析，以及它是怎麼想出來的完整過程。

一個 ReAct 架構的加密貨幣分析 Agent：從多個異質來源蒐集**原始**資料，
由模型依「證據缺口」動態決定下一步抓什麼，最後產出一份分析報告
與一份完整的推論軌跡。只分析 BTC / ETH / SOL / BNB / XRP。

---

## 60 秒上手

```bash
uv sync --extra dev

uv run python -m hoyabit_agent BTC          # 離線 demo：假資料源，秒回，看流程
uv run python -m hoyabit_agent BTC --live   # 真實資料：Binance + 新聞 RSS，免金鑰
uv run pytest                               # 344 項測試，不碰網路、不呼叫模型
```

**沒有任何 API 金鑰也完整可跑。** 資料層全部免金鑰；推理層沒設模型時會退回
腳本規劃與詞典打分，報告會誠實顯示「證據齊備但無判斷」，而不是編造內容。

### 想看真正的推理，接一個模型

```bash
cp .env.example .env      # 二選一填好，然後 export

# A. 地端（推薦：零成本、無額度限制）
#    Ollama / LM Studio / llama.cpp server 都可以，同一個 adapter 通吃。
ollama pull qwen3:8b && ollama serve
export HOYABIT_LOCAL_MODEL=qwen3:8b

# B. 雲端 Gemini 免費層 —— https://aistudio.google.com/apikey
export GEMINI_API_KEY=...

uv run python -m hoyabit_agent BTC --live
```

> ⚠️ 地端模型**必須支援 tool calling**，否則蒐集迴圈無法動態決策，
> 會直接退化成「抓不到東西」。已知可用：`qwen3`、`llama3.1`、`mistral-nemo`。

### 存下分析結果（可選）

```bash
docker run -d --name hoyabit-pg \
  -e POSTGRES_PASSWORD=hoyabit -e POSTGRES_DB=hoyabit \
  -p 5433:5432 pgvector/pgvector:pg16

uv run python -m hoyabit_agent BTC --live --save
```

**沒有資料庫也不影響分析** —— `--save` 連不到就印一行提示繼續，
需要 Postgres 的那 20 條測試會自動跳過。有資料庫時它們才真的驗證 schema。

存下來之後，一次查詢就能從判斷追到原文：

```sql
SELECT e.evidence_id, e.facet, s.text, s.url
FROM evidence e
JOIN source_excerpt s ON s.run_id = e.run_id AND s.evidence_id = e.evidence_id
WHERE e.run_id = '<run_id>' ORDER BY e.position;
```

### 把資料層掛進 MCP 客戶端

```bash
uv run hoyabit-mcp        # stdio MCP server
```

`.kiro/settings/mcp.json` 已就緒（Kiro）。Claude Desktop 或任何 MCP 客戶端也能掛，
用自然語言直接查證「這個 RSI 是用哪段 K 線算的」。

---

## 這個專案在做什麼

拿到「分析 BTC」這個請求後：

1. **幣種閘門** — 白名單比對，不在五幣內直接拒絕，不消耗任何預算。
2. **蒐集迴圈** — 模型看著「四個證據面還缺哪些」，自己決定呼叫哪些工具、帶什麼參數。
   這是真正的 ReAct，不是寫死的流程。
3. **缺口檢查** — 四面皆足、模型判定無需再挖、預算耗盡、或次數上限，四者之一即收斂。
4. **組裝** — 模型輸出**結構化判斷陣列**，引用檢核逐條過濾，過濾後才渲染成報告。

產出兩份東西：**分析報告**（方向 + 信心度 + 每句掛證據的判斷）與
**推論軌跡**（每個決策點為何這樣選、產出哪些證據、缺口如何變化）。

### 四個證據面

| 面 | 內容 | 來源 |
|---|---|---|
| **技術面** | 均線位置、RSI、量能變化 | Binance 現貨 K 線 |
| **籌碼面** | 買賣盤失衡、價差、資金費率、未平倉、多空比 | Binance 現貨盤口 + 合約 |
| **基本面** | 事件的實質影響 | 新聞 RSS |
| **情緒面** | 文本的輿論傾向 | 新聞 RSS |

證據面與資料源是**正交**的 —— 一個來源可以產出多個面的證據。

---

## 幾條不會妥協的規則

這些是設計決策，不是風格偏好。改動前請先讀對應的 ADR。

1. **無證據的判斷進不了報告。** 推理層輸出結構化物件陣列，引用檢核對陣列過濾，
   Markdown 在過濾之後才渲染。**絕不先生成散文再依標點剪裁句子** ——
   那會造成主語遺失與語法崩潰，而且不可靠。
2. **幣種閘門是白名單，不是黑名單。** 系統不判斷任何資產「是不是水幣」，
   只判斷它在不在 `Asset` 列舉內。明天出現的新幣自動被擋。
3. **信心度是證據面之間的一致程度**（[ADR 0002](docs/adr/0002-confidence-is-cross-facet-agreement.md)），
   不是模型的自我感覺。**只由有表態的面計算** —— 沉默的面不投票。
   「證據不足」與「無方向訊號」是兩個不同的第三態。
4. **轉載不構成獨立證據。** 同事件跨來源歸併後只計一個證據，但保留所有來源片段。
5. **資料源失效以空集合表達，不以例外表達。** 掛起、逾時、報錯一律等價。
6. **永不因逾時而失敗。** 預算耗盡時回傳「以現有證據組裝的報告」。
7. **工具切分只看有無外部 I/O。** 有 → MCP 工具；沒有 → Function 工具。
   這條線同時是測試邊界、故障邊界、部署邊界。

---

## 架構：四個接縫

```
                    ┌─────────────────────────────────────┐
   分析請求 ──────►  │        分析回合 (接縫 2)             │ ──► 分析報告
                    │  幣種閘門 → 規劃 → 蒐集迴圈          │ ──► 推論軌跡
                    │  → 缺口檢查 → 組裝 → 引用檢核        │
                    └───────┬─────────────────┬───────────┘
                            │                 │
                   ┌────────▼───────┐  ┌──────▼─────────┐
                   │ 證據源 (接縫 1) │  │ 模型供應者(接縫3)│
                   │  MCP 工具       │  │ 地端 / 雲端     │
                   └────────────────┘  └────────────────┘

           分析報告 + 推論軌跡 ──►  儲存 (接縫 4)：Postgres + pgvector
```

接縫 4 刻意在 `analyse` **外面** —— 分析回合的不變式是「回傳結果，不產生副作用」，
把寫入塞進去會讓接縫 2 沒有資料庫就測不動。

**所有流程測試都打接縫 2**：在接縫 1 塞假證據源、接縫 3 塞腳本假模型，
整條 pipeline 不碰網路、不燒 token 就能完整測試。

| 模組 | 角色 |
|---|---|
| [`domain.py`](src/hoyabit_agent/domain.py) | 領域型別，全部不可變 |
| [`seams.py`](src/hoyabit_agent/seams.py) | 四個接縫的介面（**不變式寫在 docstring 裡**，它們和簽章一樣是介面的一部分）|
| [`run.py`](src/hoyabit_agent/run.py) | 接縫 2：固定骨架 + 迴圈內動態 |
| [`tools.py`](src/hoyabit_agent/tools.py) | Function 工具：閘門、缺口、歸併、信心度、引用檢核 |
| [`indicators.py`](src/hoyabit_agent/indicators.py) | 技術與籌碼指標，含可稽核的算式說明 |
| [`dedup.py`](src/hoyabit_agent/dedup.py) | 同事件分群（跨媒體轉載歸併）|
| [`sources/`](src/hoyabit_agent/sources/) | 證據源適配器：Binance、新聞 RSS |
| [`models/`](src/hoyabit_agent/models/) | 模型供應者：地端 OpenAI 相容、雲端 Gemini |
| [`storage/`](src/hoyabit_agent/storage/) | 接縫 4：分析回合的持久化 |
| [`mcp_server.py`](src/hoyabit_agent/mcp_server.py) | 證據源的 MCP 暴露 |
| [`testing.py`](src/hoyabit_agent/testing.py) | 接縫 1 與 3 的假適配器 |

### 一份 `ToolSpec`，四個消費者

工具規格只定義一次（[`seams.py`](src/hoyabit_agent/seams.py)），同時餵給：

1. Gemini 的 `functionDeclarations`
2. OpenAI 相容端點的 `tools`（地端）
3. MCP 的 `inputSchema`
4. 我們自己的執行器

**因此不可能出現「模型以為的介面」與「MCP 暴露的介面」不一致。**
新增資料源時只寫一份 `spec`，四邊自動同步。

---

## 開發

```bash
uv run pytest                      # 全部測試
uv run pytest tests/test_tools.py  # 單一檔案
uv run mypy                        # strict
uv run ruff check src tests
```

**測試慣例**（新增程式碼請沿用）：

- 流程測試一律打接縫 2，不打內部。
- **測試不碰網路、不呼叫真實模型。** 外部 HTTP 一律用 `httpx.MockTransport`。
- Function 工具（`tools` / `indicators` / `dedup` / `arguments` / `models/schemas`）
  **不需要任何 mock** —— 若你發現要 mock 才能測，那東西放錯層了。
- 測試名稱描述**使用者可觀察的行為**，不是函式名稱。
- 需要 Postgres 的測試標 `@pytest.mark.postgres`，**連不到資料庫時自動跳過** ——
  這樣任何人 clone 下來 `pytest` 都是綠的。

### 版本鎖定

`mcp` **必須**寫死上界。2026-07-28 規格是 launch 以來最大的破壞性改版，
Python SDK v2 是為它重寫的。4 週內不得升 2.x。
詳見 [研究文件](docs/research/0001-tech-stack-facts.md)。

---

## 目前進度

| Ticket | 狀態 | 內容 |
|---|---|---|
| 01 端到端骨架 | ✅ | 三個接縫、報告與軌跡的資料形狀、測試慣例 |
| 02 持久化 | ✅ | Postgres + pgvector，回合/軌跡/證據/來源片段，溯源可 join |
| 03 Binance 證據源 | ✅ | 現貨 K 線 + 盤口、合約資金費率/未平倉/多空比 |
| 04 新聞 RSS 證據源 | ✅ | 基本面 + 情緒面，含跨媒體同事件歸併 |
| 05 模型供應者 | ✅ | 地端 OpenAI 相容 + 雲端 Gemini，原生 tool calling |
| 06 蒐集迴圈動態化 | ✅ | 缺口驅動、15 分鐘預算、永不逾時錯誤 |
| 07 報告組裝 | ✅ | 引用檢核、跨面一致度信心度 |
| 08 背景 ingestion | ⬜ | **未開始，時程敏感** —— 需提前上線累積資料 |
| 09 軌跡視覺化前端 | ⬜ | **未開始，評分權重最高** |
| 10 MCP server | ✅ | 已以真實 client 走 stdio 驗證 |
| 11 評估基準 | ⬜ | **未開始** —— 四項可量化門檻 |

票的完整驗收條件在 [`.scratch/analysis-agent/issues/`](.scratch/analysis-agent/issues/)。

---

## 文件索引

| 文件 | 內容 |
|---|---|
| [`PROPOSAL.md`](PROPOSAL.md) | 完整企劃書 —— 定位、架構、模型策略、風險、時程 |
| [`CONTEXT.md`](CONTEXT.md) | **領域術語表** —— 動手前先讀，這些詞都有精確定義 |
| [`docs/decisions/0001`](docs/decisions/0001-grill-decisions.md) | 十輪決策紀錄與理由 |
| [`docs/research/0001`](docs/research/0001-tech-stack-facts.md) | 版本、資料源額度、模型定價的一手查證 |
| [`docs/adr/0002`](docs/adr/0002-confidence-is-cross-facet-agreement.md) | 信心度定義與被否決的替代方案 |
| [`docs/spec/0001`](docs/spec/0001-analysis-agent.md) | 完整規格：26 條 user story |
| [`docs/design/0001`](docs/design/0001-module-shapes.md) | 模組形狀與接縫的完整介面 |

**排除清單在 [`PROPOSAL.md`](PROPOSAL.md) 第十一節** —— 那些是刻意的取捨，不是遺漏。
提新功能前請先確認它不在那份清單上。
