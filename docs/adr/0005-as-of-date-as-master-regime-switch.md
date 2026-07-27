# ADR 0005 — 分析截止日 (As-of Date) 作為總開關,推導分析模式

- **日期**：2026-07-27
- **狀態**：已接受
- **來源**：`/grill-with-docs`（`/domain-modeling`）

## 脈絡

`as_of_date`（分析截止日）此前只是 `market_dataset_context` 工具的一個**逐次呼叫參數**
（見 `ingest/historical.py`），預設 2026-05-31,而且只管一件事:限制資料集檢索的日期上界。

但三個彼此獨立的缺口,追根究柢是同一個問題:

1. **新鮮度 Bug（P1）**：`tools.py` 的 `assess_confidence` 與 `evidence_gap`
   把時效錨定在 `datetime.now()`。在歷史回測下,所有資料集證據恆為「數月前」,
   新鮮度永遠被壓在 0.2,信心度無故被扣約 16 分 —— 這在回測情境下毫無意義。

2. **資料缺口死胡同（Sol A）**：`MARKET_SUMMARY` 題型要求四面向全覆蓋,
   但競賽資料集只有 OHLCV（技術面）。關鍵字預設落入 `MARKET_SUMMARY` 的問題
   **結構上永遠無法收斂**,一律走降級路徑。

3. **偷看未來（Sol B）**：live 工具（`binance_spot`、`binance_derivatives`、
   `extended_news`…）其實**都已實作並接上** `build_competition_sources`。
   問題是在歷史回測下呼叫它們,會把**截止日之後**的即時資料灌進報告,
   直接違反 as-of 保證。

## 決策

**把 `as_of_date` 從工具參數提升為 `AnalysisRequest` 的必填一等公民,
並讓它成為驅動全系統行為的總開關。** 由它推導出一個衍生概念 **分析模式 (Analysis Regime)**:
截止日早於現在 → 歷史回測 (Backtest);否則 → 即時 (Live)。

`as_of_date` 恰好驅動四件事:

| 面向 | 目標 | 行為 |
|---|---|---|
| Domain | `AnalysisRequest` | `as_of_date` 為必填欄位,預設 2026-05-31 |
| Requirement (Sol A) | `question.derive_requirement` | 回測模式下 `MARKET_SUMMARY` 只深挖技術面,另三面標為資料不可得 (Limitation) |
| Routing (Sol B) | `runtime.build_competition_sources` / `run._invoke` | 回測模式過濾掉 live 工具,只留 `market_dataset_context` |
| Scoring (P1) | `tools.assess_confidence` / `evidence_gap` | 移除 `datetime.now()`,時效改對 `as_of_date` 相減 |

## 理由

- **統一語言**:`as_of_date`（分析截止日）已是既有詞彙。以 `requested_as_of_date`
  另立新名會把同一概念分裂成兩個字,正是 glossary 要防的事。
- **行為由時間推導,不由旗標宣告**:分析模式是**衍生概念**,不是獨立輸入。
  程式裡不塞 `is_live` 布林旗標 —— 兩種行為分歧都從 `as_of_date` vs. now 自然推導,
  單一事實來源,不可能出現「旗標說 live 但日期是歷史」的矛盾狀態。
- **回測的誠實性正是加分點**:對財金背景的評審而言,明確標記「此面向在回測下資料不可得」
  遠比硬湊四面向可信。降級不是妥協,是回測情境下**正確**的行為。
- **不重寫框架**:本決策完全在既有手寫 skeleton（`run.py`）內完成,
  不導入 LangGraph、不為用而用 LlamaIndex。既有的 live 工具已存在,
  工作是「依模式過濾」與「硬化可靠度」,不是從零開發。

## 後續

- P3（信心分歧警告:技術面與情緒面衝突 → 明確輸出「觀點分歧 / 高風險」,
  接上定期定額業務邏輯)排在這四步之後,依賴各面向能可靠填充。
- `stale_evidence`（`evidence_gap` 的 24h 硬門檻)一併改為對 `as_of_date` 相減。
