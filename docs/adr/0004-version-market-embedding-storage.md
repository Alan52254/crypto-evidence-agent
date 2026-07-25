# 市場 embedding 儲存採版本化新表

Gemini embedding 從舊 hashing 向量的 256 維改為 `gemini-embedding-001` 的 768 維，因此新建 `market_dataset_document`，以資產、分析截止日與 embedding model 作為鍵，並保留舊 `ingested_document`。這避免破壞既有資料，也禁止不同模型或維度的向量被靜默混用；日後改維度必須明確重建索引。
