# 待辦 Issues（交接用）

四個待辦（原本七個，其中軌跡前端、背景 ingestion、評估基準已於本輪完成），依建議順序排列。每一則可直接貼到 GitHub Issue。
若已安裝 [`gh`](https://cli.github.com/)，跑 `bash docs/handoff/create-issues.sh` 一次開完。

> 已完成並移除：ticket 02 持久化、09 軌跡前端、08 背景 ingestion、11 評估基準。
> 剩下的多是小 bug、選配資料源與需人工操作 Kiro 的項目。

**動手前請先讀 [`CONTEXT.md`](../../CONTEXT.md)** —— 「證據」「證據面」「判斷」
「方向」「信心度」「推論軌跡」都有精確定義，不是泛稱。

排除清單在 [`PROPOSAL.md`](../../PROPOSAL.md) 第十一節，那些是刻意的取捨。

---

## #1 — 報告缺少分析涵蓋的時間窗

`priority:medium` `bug`

`docs/spec/0001-analysis-agent.md` User Story 9：
> 作為使用者，我想知道這份分析涵蓋的時間窗，以便判斷它的時效性。

**目前 `Report` 沒有時間窗欄位，`to_markdown` 也沒印。**
時間資訊只隱含在各工具的參數裡（`interval`/`limit`/`hours`），使用者看不到。

- [ ] `Report` 帶上分析涵蓋的時間窗
- [ ] `to_markdown` 呈現它
- [ ] 時間窗由實際蒐集到的證據推導（取來源片段 `retrieved_at` 與內容時間的範圍），
      而不是宣告值 —— 否則它會跟實際資料脫節

---

## #2 — 補上 CoinGecko 與自家聚合指標兩個證據源

`priority:medium` `backend`

`docs/spec/0001-analysis-agent.md`「分層與接縫」列出四個真實適配器：
> 真實適配器：Binance 公開 REST、新聞 RSS、CoinGecko Demo、本所聚合指標（僅介面＋假資料）

**後兩者尚未實作。**

- [ ] **CoinGecko Demo** 作為補充欄位來源。⚠️ 月額度僅 10,000 次，
      **不可作為輪詢主力**（每 5 分鐘輪詢一次就會用光）
- [ ] **本所聚合指標**：只實作介面與假資料
      （分幣種成交量、買賣盤比、定期定額淨流入、新增持倉帳戶數）。
      **不接真實內部系統** —— 那會讓時程被內部資料開通流程綁架
- [ ] 兩者都遵守接縫 1 的不變式：失效回空集合、每項證據帶完整來源片段、
      硬性單次 I/O 逾時上限

**為什麼自家指標值得留介面：** 它是全場只有我們寫得出來的證據
（「本所定期定額買盤在 BTC 回檔時淨流入增加」），產品化時打開即可用。

---

## #3 — 補上 `.kiro/specs/` 架構規格

`priority:low` `docs`

ticket 10 要求 `.kiro/` 含 steering prompts、**系統架構 spec**、MCP server 設定。
目前只有 `steering/project.md` 與 `settings/mcp.json`，缺架構 spec。

- [ ] 建立 `.kiro/specs/`，以 Kiro 的 spec 格式描述系統架構
- [ ] 內容與 `docs/design/0001-module-shapes.md` 一致（三個接縫、I/O 邊界判準）

這是 AWS Kiro 加分項的實體交付物之一。

---

## #4 — 以 Kiro 載入 MCP server 並錄影

`priority:low` `demo`

ticket 10 的最後一條，**需要人工操作 Kiro**。

- [ ] 在 Kiro 開啟本 repo（`.kiro/settings/mcp.json` 已就緒）
- [ ] 用自然語言查詢資料層，例如「BTC 現在的 RSS 新聞有哪些」
      「這個 RSI 是用哪段 K 線算出來的」
- [ ] 錄影或截圖，供評審雙重驗證

**注意：** AWS Kiro 是 spec-driven 的 agentic IDE，是**開發工具**不是 runtime 模型。
2000 credits 是開發額度。加分要靠展示 Kiro 的 spec/hooks 使用，
而非宣稱系統主腦是它。
