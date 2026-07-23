# 10 — 證據源封裝為 MCP server

**What to build:** 開發者可以把本專案的證據源掛載到 Kiro 或 Claude Desktop，
在對話中直接查詢「BTC 現在的 RSS 新聞有哪些」「最近的 K 線數值」，
用來驗證資料正確性，也可在 Demo 現場即席示範資料層是真的。

**Blocked by:** 03, 04

**Status:** done

- [x] 各證據源以 MCP server 形式暴露（依 I/O 邊界判準，這些本就屬 MCP 工具）
- [x] 使用 `mcp==1.28.1`，**不得使用 2.0.0aN/bN 預發布版**
- [x] 可成功掛載並回應工具呼叫（已以真實 MCP client 走 stdio 驗證：
      initialize → list_tools → call_tool 全通，回傳 6 項帶來源片段的證據）
- [x] 工具回傳的每項結果都帶完整來源片段，與分析回合內使用時一致
- [x] MCP server 與分析回合共用同一份證據源實作，不出現兩套邏輯
- [x] **repo 根目錄含完整 `.kiro/` 規格資料夾**（steering prompts、系統架構 spec、MCP server 設定），
      作為 AWS Kiro 加分項的實體交付物
- [ ] 產出「以 Kiro 載入本專案 MCP server 並用自然語言除錯」的錄影或截圖，供評審雙重驗證
      —— **需人工操作 Kiro，尚未完成**。`.kiro/settings/mcp.json` 已就緒，
      在 Kiro 中開啟本 repo 即可掛載。
