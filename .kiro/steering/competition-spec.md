---
inclusion: auto
---

# 競賽規格摘要 — HOYA BIT AI 金寶交易管家

本文件摘錄競賽系統架構規格的關鍵約束，開發時必須遵守。
完整原文見隊友的 `hoya-bit-ai-agent-architecture-spec-zh-tw-v1-1.docx`。

---

## 硬需求（必須滿足）

| ID | 需求 | 系統回應 |
|----|------|----------|
| C-01 | 五大幣種 | 白名單 BTC/ETH/SOL/BNB/XRP；非法輸入 422 |
| C-02 | 15 分鐘限制 | 14:00 停止分析；最後 60 秒封裝；全部 ≤ 900 秒 |
| C-03 | 多源整合 | 價格加至少兩類獨立來源 |
| C-04 | 事實→推論→結論 | Claim Graph；結論有 Evidence Edge |
| C-05 | 矛盾與限制 | 反方搜尋、信心校準及推翻條件 |
| C-06 | 可回溯 | URL/Endpoint、參數、時間、片段、SHA-256 |
| C-07 | 四項提交 | Report / Evidence / Log / Source，Manifest 驗證 |
| C-08 | 不得代交投顧結論 | 只取 Raw Facts，分析文章降權 |
| C-09 | 決賽交付 | 簡報、AWS 架構圖、Demo/影片、GitHub |
| C-10 | Kiro 加分 | .kiro/specs、steering、tasks |

## 交付產物（四項必備）

1. **分析報告 (Report)** — HTML/Markdown，含 Executive Summary、關鍵判斷、Evidence 引用、正反方與矛盾、信心與限制、推翻條件、後續觀察
2. **Evidence List** — JSON/CSV，每項必含 evidence_id、source、source_url、source_type、fetched_at、content_reference、query_context、related_claim_ids、raw_artifact_uri、sha256、quality/relevance/freshness/independence scores
3. **Execution Log** — JSONL，每行含 event_id、job_id、timestamp、node、event_type、tool_name、input_summary、output_summary、duration_ms、status、retry_count、artifact_refs
4. **Source / Config (Manifest)** — Lockfile、SBOM、映像 Digest、Commit SHA、Prompt/Model/Formula Version

## 信心度公式（規格定義）

信心 = 來源品質 25% + 覆蓋 25% + 時效 20% + 一致性 20% + 完整性 10%

> 注意：目前程式碼（ADR 0002）只實作了一致性維度。規格要求的完整公式是五維加權。
> 這是已知的 gap，需要逐步補齊。

## 時間預算分配

| 節點 | 預算 | 輸出 | 失敗處理 |
|------|------|------|----------|
| Validate/Snapshot | 0:20 | Input Snapshot | Fail Fast |
| Plan | 0:40 | Research Plan | Fallback Template |
| Parallel Collect | 5:00 | Raw Artifacts | Bounded Retry / Circuit Breaker |
| Normalize/Feature | 1:30 | Silver/Features | 跳過壞來源並警告 |
| Rank/Contradiction | 1:30 | Evidence Sets | 規則式 Fallback |
| Claim Synthesis | 2:00 | Claim Graph | 縮小 Context 重試一次 |
| Report | 1:30 | Draft | 模板式報告 |
| Verify/Export | 1:30 | Final Artifacts | Partial + Manifest |

總目標 13:30；14:00 停止新分析；最後 60 秒只做封裝。

## 報告規範

- 每份報告標示 As-of Time、UTC、單位及「不構成投資建議」
- 清楚區分「事實、推論、結論」三層
- 所有結論必須附證據、資料時間、限制及可能推翻結論的條件
- 主要結論目標兩個獨立來源
- 禁止「保證、必漲、必跌」
- 市場數值全部標示 UTC、單位、資料取得時間與 Freshness

## Evidence 品質評分維度

每項 Evidence 必須有可分解的分數：
- **獨立性 (independence)** — 是否來自獨立來源
- **時效性 (freshness)** — 資料多新
- **品質 (quality)** — 來源可靠度
- **相關性 (relevance)** — 與分析題目的關聯度

## Execution Log 安全規範

寫入前必須遮罩：
- Secret / API Key
- Authorization header
- 完整 Prompt
- 私密 Reasoning / Chain-of-Thought

只公開：節點摘要、工具名稱、來源與採用/捨棄理由。

## 非功能需求

| ID | 需求 | SLO |
|----|------|-----|
| NFR-01 | 正式 Job | P95 ≤ 14 分鐘；全部 ≤ 15 |
| NFR-02 | 一般 API | P95 < 400ms |
| NFR-03 | 可重現 | 各版本寫入 Manifest |
| NFR-04 | 可稽核 | 100% 結論有 Evidence |
| NFR-05 | 韌性 | 非必要來源失敗仍有 Partial |
| NFR-06 | 安全 | TLS/KMS/IAM/WAF/SCA |
| NFR-07 | 成本 | 記錄 Cost per Job |

## 功能需求重點

| ID | 功能 | 驗收 |
|----|------|------|
| FR-04 | 平行取得至少三類資料並保存 Raw | 單來源失敗可降級 |
| FR-05 | 技術特徵由程式計算，禁止 LLM 心算 | Golden Tests |
| FR-06 | Evidence 保存來源、時間、片段、查詢及 Hash | Schema 100% |
| FR-07 | 評分獨立性、時效、品質及相關性 | 分數可分解 |
| FR-08 | 建立支持及反對證據並辨識矛盾 | 至少一個反方分支 |
| FR-09 | 輸出判斷、依據、信心及限制 | Report Lint |
| FR-11 | 支援取消、Timeout 及 Partial | Deadline Test |

## 資料集規格

- 五幣種 Daily OHLCV CSV，2021-06-01 至 2026-05-31
- 每幣種 1,826 筆，總計 9,130 筆
- 交易對 USDT，Volume 為 Base Asset
- 欄位：date、open、high、low、close、volume
- **OHLCV 不向量化** — 用 Pandas/NumPy 計算技術指標
- LlamaIndex 只索引新聞、公告與研究長文
- 主辦 CSV 只用於歷史背景；任何「當前市場」結論都必須補抓新鮮行情並顯示 As-of Time

## 產品邊界（不做的事）

- 自動代客投資
- 無確認交易
- 非白名單幣種分析
- 公開模型私密 Chain-of-Thought
- 依賴單一投顧結論
- 競賽 MVP 不執行真實交易

## 技術棧（規格書定義 vs 實際實作）

| 領域 | 規格書定義 | 實際實作 | 差異說明 |
|------|-----------|---------|----------|
| 前端 | Next.js 16.2 + React 19 | Next.js 15.5 + React 18 | 版本較舊但穩定可用 |
| API | FastAPI + Pydantic v2 | Starlette (輕量) | 隊友選更輕量方案 |
| Agent | LangGraph 唯一控制平面 | 自寫 run.py (200 行) | 可完全掌控，不受框架波動 |
| 模型 | Amazon Bedrock | Gemini Flash (免費層) | 成本考量，免費開發 |
| 資料 | PostgreSQL + pgvector + S3 | PostgreSQL + pgvector | 無 S3，本地儲存 |
| 部署 | ECS Fargate | 本地開發 | 尚未部署 |

> 規格書是競賽提交的理想架構，實際程式碼因時間與資源限制做了取捨。
> 兩者的核心邏輯（三層判斷、證據可溯源、多源整合、15 分鐘限制）是一致的。

## Kiro 加分項（C-10）

- `.kiro/specs/` — 系統架構 spec（已完成）
- `.kiro/steering/` — 開發規則與知識庫（已建立）
- `.kiro/settings/mcp.json` — MCP server 設定（已完成）
- 展示 Kiro 的 spec/hooks 使用
- 用自然語言查詢資料層並截圖/錄影
