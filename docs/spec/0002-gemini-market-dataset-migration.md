# Gemini 市場資料集遷移規格

- `models/` 的正式推理只使用 `gemini-3.6-flash`，不得使用本機 OpenAI 相容模型。
- ingestion 只讀競賽資料集的 BTC、ETH、SOL、BNB、XRP daily OHLCV CSV，不抓 Binance 或 RSS。
- 每個資產、每個 UTC 日建立最近最多 30 日的窗口；前 29 日標記 `window_complete=false`。
- 原始數值以 `Decimal` 解析；資料不足、除以零或非有限指標為 `null`，Gemini 不得補值。
- 指標固定為 daily/7d/30d return、30d 年化樣本波動度、SMA7/30、Wilder RSI14、1d 成交量變化與 30d 成交量均值比。
- embedding 使用 `gemini-embedding-001`、768 維；文件使用 `RETRIEVAL_DOCUMENT`，查詢使用 `RETRIEVAL_QUERY`。
- 所有檢索帶 `as_of_date`，預設與上限均為 2026-05-31，且不得取回未來文件或宣稱即時行情。
- OHLCV 只能支持 technical 證據；新聞、基本面、鏈上、籌碼、情緒與超出日期範圍的問題回報證據不足。
- 新建 `market_dataset_document`，保存來源檔、列範圍、窗口、指標、embedding model 與維度；保留舊表。
- 真實 key 只存在被忽略的 `.env`；`.env.example` 只提供 placeholder。
