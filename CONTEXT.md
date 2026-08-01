# HoyaBit 加密貨幣分析 Agent

一個 ReAct 架構的 Agent，在 15 分鐘內從多個異質來源蒐集原始資料，
推論出一份**每句判斷都可溯源到原始片段**的加密貨幣分析報告。

## Language

### 標的與範圍

**受涵蓋幣種 (Covered Asset)**：
系統允許分析的五種資產：BTC、ETH、SOL、BNB、XRP。任何不在此集合內的資產一律拒絕分析。
_Avoid_: 主流幣、五大幣、標的（後者太泛）

**受排除幣種 (Excluded Asset)**：
明確落在受涵蓋幣種之外的資產。**這是集合的補集，不是一個品質判斷** ——
系統不判斷任何幣「是不是水幣」，只判斷它在不在名單內。
_Avoid_: 迷因幣、水幣、山寨幣（這些是主觀分類，無法寫成程式判準）

**幣種閘門 (Asset Gate)**：
在推理開始**之前**執行的硬性檢查，確認請求的標的是受涵蓋幣種，否則短路整個流程。
它是寫死的程式判斷，不是交給模型的指示。
_Avoid_: 護欄、Guardrail、Validation Gateway（太泛，未指出它在推理之前）

### 證據

**證據 (Evidence)**：
一個帶有穩定識別碼的、不可變的事實單元，記錄一個可被驗證的觀察，
並保有回到其**來源片段**的完整路徑。證據是報告中每一句判斷的唯一合法依據。
_Avoid_: 資料、data point、chunk、素材

**來源片段 (Source Excerpt)**：
證據所指向的原始文本或數值本身，連同它的出處 URL／端點、擷取時間、以及在原文中的位置。
_Avoid_: 原文、chunk、snippet、context

**證據面 (Evidence Facet)**：
證據所屬的分析面向。恰好四種，互斥且窮盡：

- **技術面 (Technical)** — 由價量時間序列計算得出的數值（均線、RSI、成交量變化）。
- **籌碼面 (Positioning)** — 反映資金與持倉分布的數值（交易所淨流入流出、資金費率、未平倉合約、本所聚合流向）。
- **基本面 (Fundamental)** — 關於該資產本身或其網路的事實（升級、解鎖、監管事件、鏈上活動）。
- **情緒面 (Sentiment)** — 從人類撰寫的文本推導出的傾向分數，永遠附帶產生它的來源片段。

_Avoid_: 維度、類別、data source type（「面」對應命題原文的用語，且與資料源無關 ——
同一個資料源可以產出多個面的證據）

**情緒分數 (Sentiment Score)**：
對**單一則來源片段**推導出的傾向值，落在 −1（極空）到 +1（極多）。
它永遠屬於一則片段，不存在「BTC 的情緒分數」這種東西 ——
那是**情緒彙總 (Sentiment Aggregate)**，由多個情緒分數加權而成，且必須能列舉出組成它的每一則片段。
_Avoid_: 情緒指標、sentiment index（會誤導成單一數字）

### 推論與輸出

**分析請求 (Analysis Request)**：
對某個受涵蓋幣種發起一次分析的意圖，包含標的與發起時間。
_Avoid_: query、問題、任務

**分析回合 (Analysis Run)**：
處理一則分析請求的完整生命週期，從幣種閘門到報告產出，有唯一識別碼與 15 分鐘的壁鐘上限。
一則分析請求恰好對應一個分析回合。
_Avoid_: session、job、執行

**蒐集迴圈 (Gathering Loop)**：
分析回合中真正動態的部分：模型依據「目前的證據缺口」自行決定下一步呼叫哪些工具，
直到證據足夠或預算用罄。這是 ReAct 的所在之處。
_Avoid_: LoopAgent、ReAct loop、Agent loop（前者是產品名不是概念）

**證據缺口 (Evidence Gap)**：
四個證據面中，尚未蒐集到足夠證據以支撐判斷的那些面。它是蒐集迴圈的終止條件，
也是模型決定下一步的依據。
_Avoid_: missing data、待補資料

**判斷 (Claim)**：
報告中一個表達立場的句子。**每個判斷必須掛載至少一個證據識別碼**；
未掛載證據的判斷在組裝階段被丟棄，不會出現在報告中。
_Avoid_: 結論、論點、statement

**方向 (Stance)**：
一份報告的整體傾向，恰好三值之一：偏多、偏空、中性。
_Avoid_: 多空、訊號、signal、建議（「建議」帶有投資顧問意涵，刻意避開）

**信心度 (Confidence)**：
衡量**各證據面之間彼此一致的程度**，而非模型的主觀把握。
四個面一致指向同方向 → 高；技術面與情緒面互相矛盾 → 低。
低信心度本身是報告的重要輸出，不是缺陷。
_Avoid_: 準確度、probability、確定性

**分析報告 (Analysis Report)**：
一次分析回合的最終產物：一個方向、一個信心度、以及一組全部掛載了證據的判斷。
_Avoid_: 報表、投資建議、研報

### 可觀測性

**推論軌跡 (Trace)**：
一次分析回合中所有決策點的完整、有序紀錄，本身是交付物而非除錯副產品。
每個節點記錄：為何選擇這個動作、輸入、產出的證據識別碼、以及它如何改變證據缺口。
_Avoid_: log、CoT、span、除錯輸出

**軌跡節點 (Trace Node)**：
推論軌跡中的單一決策點。軌跡節點與證據之間的連線，就是評審要看的「點對點之間為什麼」。
_Avoid_: step、event、span

### 工具

**MCP 工具 (MCP Tool)**：
跨行程邊界、執行外部 I/O 的能力。判準只有這一條：有沒有外部 I/O。
_Avoid_: 資料源工具、外部工具

**Function 工具 (Function Tool)**：
行程內、確定性、無 I/O 的純計算。可在無 mock 的情況下完整測試。
_Avoid_: 內部工具、helper、util

## 競賽市場資料

**市場資料窗口 (Market Data Window)**：
單一受涵蓋幣種在某個分析截止日可知的最近最多 30 個 UTC 日 OHLCV，以及由程式確定性計算的技術指標。
_Avoid_: 新聞文件、即時行情、Gemini 產生資料

**分析截止日 (As-of Date)**：
一次分析的**時間立足點**：允許使用資料的最後 UTC 日期。它是**分析請求的必填一等公民**，
未指定時預設為資料集截止日 2026-05-31。它是全系統的總開關，恰好驅動四件事：
(1) 推導**分析模式**；(2) 決定哪些資料源合規（回測模式禁用 live 工具，防止偷看未來）；
(3) 各證據面的缺口需求（回測且僅有 OHLCV 時，市場摘要題不強求四面）；
(4) 新鮮度的參考點（時效永遠對 As-of Date 相減，不對現實時鐘）。
_Avoid_: 今日、即時、最新行情、requested_as_of_date（同一概念不可分裂命名）

**分析模式 (Analysis Regime)**：
由分析截止日推導出的行為分歧，非獨立輸入：截止日早於現在為**歷史回測 (Backtest)**，
否則為**即時 (Live)**。系統的行為分歧一律由它推導，而非在程式裡塞 `is_live` 布林旗標。
_Avoid_: is_live、mode 旗標、歷史開關（這些把「可推導的衍生概念」誤當成「獨立輸入」）

**資料集證據 (Dataset Evidence)**：
可由競賽 OHLCV 原始列與可重現指標直接支持的技術面證據，必須帶來源檔、列範圍與日期。
_Avoid_: 模型知識、新聞證據、無來源推論

**暖機窗口 (Warm-up Window)**：
資料集開頭少於 30 日的市場資料窗口；資料不足的指標為 null，且不得由模型補值。
_Avoid_: 缺失資料、估算窗口

**證據不足 (Insufficient Dataset Evidence)**：
問題超出資料集日期或要求新聞、基本面、鏈上、籌碼或情緒資訊時的明確結果。
_Avoid_: 猜測、外部常識補完

## 已知架構風險（2026-08 Bedrock 遷移後）

### 範圍偷換偵測 (Scope Swap Detection) — 無專屬結構性防線

**現狀**：系統沒有 deterministic 的 scope swap detection 模組。
當題目問「A 對 B 的影響」但蒐集到的證據只涵蓋 B 本身（而非 A→B 的因果鏈），
目前完全依賴模型自身的判斷力來辨識並揭露此落差。

**已驗證的情境**：
- 完全不相關的極端案例（證據全是 BTC 技術面，題目問美股影響）→ Claude 正確拒絕編造，明確聲明缺乏宏觀資料。
- 部分相關的美股範圍偷換（證據有 FEDFUNDS/M2/相關性數據但無美股本身指標）→ Claude 未偷換，但這是模型自己守住的，非系統強制。

**未被覆蓋的風險**：
- 漸進式範圍窄化（證據「部分相關但範圍被悄悄縮小」）— 比「完全不相關」難偵測得多。
  模型在此情境下可能用「宏觀環境」代替「美股本身」來回答，從結果看起來合理但實質偏離了問題。
- 目前無 deterministic 防線攔截此類偷換。gap_rules 和 confidence penalty 只懲罰「缺少面向」，
  不檢查「蒐集到的證據主題是否真的對應問題主題」。

**維護建議**：
- 不要因為目前測試中 Claude 沒犯此錯就假設此風險不存在。
- 若未來觀察到報告偷換範圍的案例，應優先建立 deterministic 的 scope relevance check
  （可能作為 claim_ledger 的擴充：驗證 conclusion 引用的 evidence 是否涵蓋問題的核心概念）。
- 這是「依賴模型能力、缺乏結構性保障」的已知妥協，不是遺漏。

### Review 層在 Bedrock 架構下被跳過

**現狀**：`BedrockProvider` 不支援 `_text_generation_channel()`（它是 Gemini 專屬的 `_post()` 方法），
因此 Layer 3 review（語氣修飾、面向矛盾解釋）自動跳過。

**影響**：
- `review_applied: False` 會被記錄在報告 metadata 中（結構化揭露）。
- 推論軌跡明確記錄「review layer skipped: provider does not support text generation channel」。
- Phase 2 驗證結論：Claude Sonnet 4.6 在 synthesise 階段自然處理矛盾（5 次測試穩定），
  review 層的核心功能已被模型底層能力覆蓋。
- 但若未來切換到推理能力較弱的模型，review 層可能需要重新啟用。

### enforce_paired_disclosure 在 Bedrock 上的狀態

**現狀**：deterministic function，與模型無關。interface 是 `tuple[DraftClaim, ...]`。

**已驗證**：函數邏輯本身以 unit test 確認正確 — 手動構造只引用 SMA60 的 DraftClaim
（使用與 Bedrock 輸出相同的 evidence ID 命名格式 BNC-SPOT-XXX-SMA60），
函數正確注入 SMA200 的 evidence_id 與摘要文字。這證明「如果 Claude 產出只引用
單邊指標的 claim，此函數會接住」。

**尚未驗證**：在真實 Bedrock 端到端 pipeline 中，此函數從未被實際觸發過。
原因是 Claude Sonnet 4.6 的指令跟隨度較高，目前所有測試中它都自然引用了
配對指標的雙方，導致函數的 `if cited_members and missing_members` 條件
從未成立。因此「Claude 真實產出 → enforce_paired_disclosure 觸發 → 正確補上遺漏」
這條端到端路徑是假設性的，不是觀察到的事實。

**結論**：此函數是 model-agnostic 的最後一道 deterministic 保障。
函數本身可靠（unit test），但「它在真實運行中有沒有機會被需要」仍是未知。
不要因為「目前沒被觸發」就移除它 — 它的價值正是在模型偶爾失手時才顯現。
