# crypto-evidence-agent

Gemini 驅動的加密市場證據 Agent，支援 BTC、ETH、SOL、BNB、XRP。正式回合會把主辦方 Daily OHLCV、即時市場／衍生品資料及公開新聞來源整合成可回溯的分析；缺少鏈上、社群或基本面證據時會明確標示「證據不足」，不從價格資料捏造事件。

## 核心保證

- 推理模型固定為 `gemini-3.6-flash`。
- embedding 使用 `gemini-embedding-001`、768 維；文件與查詢分別採 `RETRIEVAL_DOCUMENT`、`RETRIEVAL_QUERY`。
- 五份 `*_daily_ohlcv.csv` 每個幣種、每個交易日建立一份最多 30 日的文件。
- 指標資料不足時為 `null`；Gemini 不得補值。
- 歷史檢索限制 `as_of_date <= requested_as_of_date`，預設截止 `2026-05-31 UTC`，不稱為即時行情。
- 每個保留判斷必須引用 Evidence ID；URL／檔案、取得時間、locator、引用片段及執行軌跡皆可追溯。
- 15 分鐘總預算、單一來源 timeout、失敗降級與限制揭露。
- 比較題可在同一回合針對兩個指定資產分別呼叫工具。

## 設定

```powershell
uv sync --extra dev
Copy-Item .env.example .env
```

真實金鑰只放在被 `.gitignore` 排除的 `.env`：

```dotenv
GEMINI_API_KEY=your-gemini-api-key
GEMINI_MODEL=gemini-3.6-flash
GEMINI_EMBEDDING_MODEL=gemini-embedding-001
GEMINI_EMBEDDING_DIMENSIONS=768
```

## 匯入主辦方資料

啟動含 pgvector 的 Postgres 後執行 `uv run hoyabit-ingest`。可用 `--dataset PATH` 指定資料集。系統建立 `market_dataset_document`、保留舊 `ingested_document`，以 `(asset, as_of_date, embedding_model)` 冪等 upsert。更換向量維度時必須明確重建索引。

## 正式分析與提交物

```powershell
uv run python -m hoyabit_agent BTC --live --save --question "市場上認為 BTC 短期盤整，請驗證支持與反對證據" --output-dir submissions
```

每次 live 回合輸出：

- `final_report.md`：市場判斷、關鍵依據、限制與引用。
- `evidence_list.json`：來源、URL、取得時間、引用區間、來源層級與對應判斷。
- `execution_log.json`：時間序列、工具呼叫、資料取得、缺口與分析流程。

可視化介面：`uv run hoyabit-viz --host 127.0.0.1 --port 8000`。

## 驗證

```powershell
uv run --extra dev pytest
uv run --extra dev mypy src
uv run --extra dev ruff check src tests
```

詳見 [CONTEXT.md](CONTEXT.md)、[遷移規格](docs/spec/0002-gemini-market-dataset-migration.md) 與 [Gemini／資料邊界 ADR](docs/adr/0003-gemini-only-market-dataset-analysis.md)。本專案輸出不構成投資建議。
