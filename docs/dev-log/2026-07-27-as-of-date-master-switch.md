# 開發日誌：as_of_date 總開關 — Domain、新鮮度、限制揭露、來源過濾、題型邊界

**日期**：2026-07-27
**分支**：`feature/as-of-date-master-switch`

## 本次進度

把「分析截止日 (As-of Date)」從一個工具參數提升為驅動全系統的總開關，
修掉一個真實存在的信心度 bug（新鮮度永遠對現實時鐘算），並把系統對自己
能力邊界的誠實聲明從推論軌跡搬進評審實際會讀的報告本體。詳見
[ADR 0005](../adr/0005-as-of-date-as-master-regime-switch.md)。

## 為什麼要做這件事

命題原文明確要求「點對點之間為什麼結果這樣出來」、「不是只是問一個投顧」——
評審看的是推理的層次與誠實度，不是預測準不準。三個獨立症狀，追根究柢
是同一個問題：

1. **新鮮度 bug**：信心度計算把證據時間跟 `datetime.now()` 相減。回測時，
   所有資料集證據恆為「數月前」，新鮮度永遠壓在下限，信心度無故被扣分——
   這在回測情境下毫無意義，且會讓評審誤以為系統的信心度演算法有問題。
2. **資料缺口死胡同**：競賽資料集只有 OHLCV（技術面），但市場摘要題型
   要求四面向全覆蓋。這個組合在回測下**結構上永遠無法收斂**。
3. **偷看未來**：籌碼面、情緒面的 live 來源（Binance、RSS）其實都已實作，
   但沒有機制阻止它們在回測分析中被呼叫——這會讓歷史分析摻入未來資料。

## 改動內容

| 層級 | 檔案 | 說明 |
|------|------|------|
| Domain | `src/hoyabit_agent/domain.py` | 新增 `AnalysisRequest.as_of_date`（必填，預設 2026-05-31）、`AnalysisRegime` enum（BACKTEST/LIVE）、純函數 `analysis_regime(as_of_date, *, today)`；`Report.limitations` 一等欄位 |
| 新鮮度 | `src/hoyabit_agent/tools.py` | `assess_confidence` 新增 `as_of` 參數，`as_of_reference()` 提供 end-of-day UTC 參考點，取代 `datetime.now()` |
| 時間洩漏修復 | `src/hoyabit_agent/ingest/historical.py` | 資料集證據 `retrieved_at` 改蓋在其代表日的 UTC 收盤，不再蓋現實時鐘（否則歷史證據會「來自未來」） |
| 題型規則 | `src/hoyabit_agent/question.py` | `derive_requirement` 依 `regime` 調整：回測模式市場摘要題只要求技術面，另三面記為 `unavailable_facets`；新增 `boundary_notes`（未命中題型 / 預測題劃界） |
| 編排 | `src/hoyabit_agent/run.py` | `analyse()` 推導 regime（`today` 可注入）、依 regime 過濾證據源 registry、把 `unavailable_facets` 與 `boundary_notes` 貫穿進 `Report.limitations` |
| 報告渲染 | `src/hoyabit_agent/domain.py`、`report_enhanced.py` | `to_markdown()`（評審看的 `final_report.md`）與前端渲染皆改為動態呈現 `limitations` |
| 持久化 | `storage/schema.sql`、`storage/postgres.py` | `analysis_run.limitations` JSONB 欄位，save/load 完整往返 |
| 來源過濾 | `seams.py`、`sources/*.py`、`run.py` | `EvidenceSource.supported_regimes`；回測模式下 live 來源連工具清單都不會出現在模型面前 |
| 入口修復 | `cli.py`、`viz/server.py` | `--live` 與前端 `/api/v1/analyse` 預設 `as_of_date` 為今天，避免預設值悄悄把即時 demo 退化成回測模式 |

## 前後差異對照

| 項目 | 之前 | 之後 |
|------|------|------|
| 新鮮度計算基準 | `datetime.now(UTC)` | `as_of_date`（回測時取當日 UTC 收盤） |
| 資料集證據時間戳 | 抓取當下的現實時鐘 | 該筆資料代表日的 UTC 收盤 |
| 回測信心度 | 新鮮度恆壓在下限（系統性低估） | 反映真實證據品質 |
| 市場摘要題（回測） | 要求四面向，結構上永遠無法收斂 | 只要求技術面，另三面誠實列為限制 |
| 報告的「已知限制」 | 前端寫死「社群情緒未接入」等固定清單（live 模式下會自打嘴） | 由本回合實際計算動態產生 |
| `final_report.md`（評審看的） | 完全不含任何限制說明 | 含限制章節（資料不可得的面、未關閉缺口、邊界聲明） |
| 回測模式下的 live 工具 | 可被模型呼叫（無防護） | 從模型可見的工具清單中移除 |
| 未命中已知題型的問題 | 靜默當成市場摘要，無任何說明 | 明確聲明「題型未明確匹配，以現況研判作答」 |
| 預測型問題（如「預測下週走勢」） | 靜默給出方向研判，未聲明限制 | 明確聲明「本系統輸出當前方向研判，不做未來價格預測」 |
| `--live` / 前端即時分析 | 未設定截止日，預設值使 regime 恆為回測，live 來源被自己的過濾器悄悄擋下 | 預設今天，即時來源正常被呼叫 |

## 一個重要澄清

「不做未來價格預測」**不是命題白紙黑字的規定**，而是從系統既有的可溯源
不變式（每句判斷必須掛得到來源片段）推導出的設計選擇：未來沒有片段可掛，
因此誠實聲明邊界比硬給一個無依據的預測更符合命題「不是投顧」的精神。
系統仍會給出方向研判（偏多/偏空/中性），只是不承諾具體的未來走勢或目標價。

## 測試

`tests/test_as_of_date.py`（新增，21 項）+ `tests/test_postgres_store.py`
（新增 1 項限制往返測試）+ `tests/test_stream_api.py`（新增 1 項 regime
預設測試）。全套對真實 Postgres 執行 **476 passed, 0 skipped**。

## 未動到的部分

- 條件式前瞻推論（例如「若動能持續，技術面指向繼續上行」）尚未實作，
  是目前劃界之後**可選擇性加**的下一層，而非已封死的路。
- 跨幣種比較層、金寶桌寵前端、真實聚合指標串接——仍在範圍外（見 ADR 0001）。
