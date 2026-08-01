---
inclusion: auto
---

# 圖表視覺分析規則

1. 讀取圖表時，必須明確標註數據來源（圖片 URL / 頁面來源）與擷取的數據點，不可憑空臆測。
2. 若圖表解析度不佳或無法辨識具體數值，必須誠實聲明「數據模糊，僅能判斷趨勢」，不得產生幻覺數值。
3. 圖表證據的 evidence_id 前綴為 CHART- 或 WEBCHART-，在報告中引用時讀者可據此識別來源類型。
4. 能從結構化 API 取得的數據，優先用 API，不讀圖。圖表分析是 Layer 2 fallback。
5. 單次分析回合最多處理 5 張圖表截圖，超過時取最相關的 5 張。
6. web_chart_capture 只對 CHART_REGISTRY 中的預定義來源截圖，不對任意 URL 截圖（安全考量）。
7. 截圖失敗（timeout、selector 找不到）以空集合表達，不以例外中斷分析。
8. 凡引用圖表 Evidence（ID 前綴 CHART- 或 WEBCHART-）的判斷，在研報文字中必須明確標註
   「從資料來源【圖】中得知」或「根據 [Evidence_ID] 之資料來源【圖】中得知」。
   不得省略此標記，不得將圖表數據偽裝成結構化 API 來源。
9. 新聞爬蟲偵測到頁面含圖表時，截圖自動存入 /tmp/scraped_charts/ 並綁定 source_id，
   Agent 可在後續回合引用該截圖進行 chart_reader 解析。每頁最多擷取 3 張圖表。
