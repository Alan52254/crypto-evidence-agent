# 待辦 Issues（交接用）

七個待辦，依建議順序排列。每一則可直接貼到 GitHub Issue。
若已安裝 [`gh`](https://cli.github.com/)，跑 `bash docs/handoff/create-issues.sh` 一次開完。

> ticket 02（持久化）原本是這份清單的第二項，**已完成**並移除。

**動手前請先讀 [`CONTEXT.md`](../../CONTEXT.md)** —— 「證據」「證據面」「判斷」
「方向」「信心度」「推論軌跡」都有精確定義，不是泛稱。

排除清單在 [`PROPOSAL.md`](../../PROPOSAL.md) 第十一節，那些是刻意的取捨。

---

## #1 — 軌跡視覺化前端（ticket 09）

`priority:high` `frontend`

**為什麼優先做這個：** 評審實際會盯著看的畫面，計分權重最高。
命題原文要求「看到不同 Agent 架構下，他的點對點之間為什麼結果這樣出來」——
這個頁面就是那句話的具體兌現。目前軌跡只有 CLI 的文字輸出。

打開網頁、輸入分析回合識別碼，看到這次推理的完整過程。

- [ ] 時間軸與樹狀兩種視圖
- [ ] 每個軌跡節點顯示：動作理由、模型自己選的工具參數、產出證據、缺口變化
- [ ] **證據 → 判斷的連線可視化** —— 這是「點對點之間為什麼」的核心
- [ ] 點擊任一證據展開原始來源片段、出處 URL、擷取時間
- [ ] 顯示被引用檢核丟棄的判斷並標明原因
- [ ] 時間軸顯示 15 分鐘預算的消耗分布
- [ ] 分析進行中可看到即時進度與已蒐集證據數

**依賴已解除：** ticket 02 已完成，`PostgresAnalysisStore.load(run_id)` 可直接
取回完整回合（報告 + 軌跡 + 證據 + 來源片段），`recent()` 給可看的回合清單。

完整驗收條件見 `.scratch/analysis-agent/issues/09-trace-viewer.md`。

---

## #2 — 背景 ingestion 與向量檢索（ticket 08）

`priority:high` `backend` `time-sensitive`

⚠️ **時程敏感：這張票做完 ≠ 有用。** 它必須**提前上線持續運行**，
Demo 當天向量庫才不是空的。建議 Demo 前至少一到兩週就讓它跑起來。

- [ ] 排程輪詢持續將新聞與市場數據寫入 pgvector
      （最笨但可靠的做法，不引入串流基礎設施）
- [ ] 輪詢頻率不得超出各資料源額度。**Binance 為輪詢主力；
      CoinGecko 月額度僅 10,000 次，只能當補充欄位**
- [ ] 向量檢索作為 MCP 工具暴露，回傳的每則結果都帶完整來源片段
- [ ] 重複內容在寫入前去重（可重用 `dedup.assign_event_keys`）
- [ ] 分析回合可同時使用歷史檢索結果與即時證據，兩者在報告中可區分
- [ ] 向量庫為空時分析回合仍能正常完成

**依賴已解除：** ticket 02 已完成。

---

## #3 — 評估基準與成績單（ticket 11）

`priority:medium` `quality`

跑一個指令產出成績單，量化回答「這套 Agent 到底好不好」。
這也是提案中「為什麼選這個模型」的實證支撐 —— 不是用講的，是有數字的。

- [ ] **多輪 tool calling 成功率**（10 步內不迷失、不重複呼叫），門檻 ≥95%
- [ ] **單次分析壁鐘時間**，門檻 ≤15 分鐘（命題硬性規定）
- [ ] **引用忠實度**：抽樣判斷，掛載的證據確實支撐該句，門檻 ≥90%
- [ ] **單次分析成本**，門檻 ≤US$1
- [ ] 可對不同模型跑同一組基準並比較
- [ ] 成績單輸出為可直接放進提案的格式

**特別有價值的用途：** 拿它來量地端模型（`qwen3` 等）到底夠不夠格。
多輪 tool calling 成功率是最關鍵的一項 —— 小模型容易在長迴圈中迷失。

---

## #4 — 報告缺少分析涵蓋的時間窗

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

## #5 — 補上 CoinGecko 與自家聚合指標兩個證據源

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

## #6 — 補上 `.kiro/specs/` 架構規格

`priority:low` `docs`

ticket 10 要求 `.kiro/` 含 steering prompts、**系統架構 spec**、MCP server 設定。
目前只有 `steering/project.md` 與 `settings/mcp.json`，缺架構 spec。

- [ ] 建立 `.kiro/specs/`，以 Kiro 的 spec 格式描述系統架構
- [ ] 內容與 `docs/design/0001-module-shapes.md` 一致（三個接縫、I/O 邊界判準）

這是 AWS Kiro 加分項的實體交付物之一。

---

## #7 — 以 Kiro 載入 MCP server 並錄影

`priority:low` `demo`

ticket 10 的最後一條，**需要人工操作 Kiro**。

- [ ] 在 Kiro 開啟本 repo（`.kiro/settings/mcp.json` 已就緒）
- [ ] 用自然語言查詢資料層，例如「BTC 現在的 RSS 新聞有哪些」
      「這個 RSI 是用哪段 K 線算出來的」
- [ ] 錄影或截圖，供評審雙重驗證

**注意：** AWS Kiro 是 spec-driven 的 agentic IDE，是**開發工具**不是 runtime 模型。
2000 credits 是開發額度。加分要靠展示 Kiro 的 spec/hooks 使用，
而非宣稱系統主腦是它。
