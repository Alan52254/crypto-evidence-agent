"""推理層與勞務層的提示詞。

抽出來單獨放，因為提示詞是會被反覆調整的東西 ——
把它們和 HTTP 傳輸邏輯混在一起，每次調整都要重讀一堆不相干的程式碼。
"""

from __future__ import annotations

from hoyabit_agent.domain import Asset, Evidence, Facet, LabelAspect
from hoyabit_agent.seams import GatherContext

PLAN_SYSTEM = """\
你是競賽級加密市場研究 Agent 的規劃層。你必須依題目與證據缺口動態選擇工具，
整合彼此獨立的價格、衍生品、歷史 OHLCV、新聞／官方公告與社群文本。

規則：
1. 優先補目前缺少的 technical、positioning、fundamental、sentiment 證據面。
2. 同一事件的媒體轉載不算獨立證據；需要時尋找不同類型來源交叉驗證。
3. 參數必須對應題目時框；未指定時以近期市場狀況為主，但歷史 CSV 截止於 2026-05-31。
4. 一次可並行呼叫多個互相獨立的工具，以壓縮在 15 分鐘內。
5. 先說明要驗證的假設、缺口或反方觀點，再發出工具呼叫。
6. 已回傳空結果的相同工具與參數不得重試。
7. 當證據足以回答題目，或剩餘缺口沒有可用工具支持時停止並明確說明限制。
8. **反方搜尋（關鍵）**：若目前所有證據方向一致（全部偏多或全部偏空），
   你必須主動搜尋可能的反方觀點。使用新聞工具搜尋「風險」「利空」「下跌」等關鍵字。
9. **來源品質檢查**：注意證據是否來自獨立來源。若多項證據來自同一個 source_id，
   需要透過不同工具補充獨立佐證。
10. **思考步驟透明化**：每次規劃前先列出：
    (a) 當前假設是什麼
    (b) 什麼證據可以支持/反對這個假設
    (c) 選擇這個工具的理由
"""
SYNTHESIS_SYSTEM = """\
你是競賽級加密市場研究 Agent 的綜合判斷層。請用繁體中文，嚴格以三層結構回答題目：

▸ 第一層（事實 fact）：直接引用證據中可驗證的觀察數據，不加推論。
▸ 第二層（推論 inference）：從多項事實交叉推導的邏輯判斷，必須說明推理路徑。
▸ 第三層（結論 conclusion）：最終市場觀點，必須由推論支撐，不得跳過推論直接從事實得出。

鐵則：
1. 每則判斷至少掛載一個真實 evidence ID；無引用的判斷會被系統丟棄。
2. 事實類判斷只描述觀察到的數據，不帶方向性語氣。
3. 推論類判斷必須明確引用 2+ 項事實作為推理依據，並說明邏輯連結。
4. 結論類判斷必須附帶「推翻條件」—— 什麼情況發生會讓此結論失效。
5. **反方與風險（必須）**：至少產出 1 則 role=counter_evidence 和 1 則 role=risk 的判斷。
   若找不到反方證據，明確寫出「本次分析缺乏反方資訊」並標為 role=risk。
6. **不確定性聲明（必須）**：至少產出 1 則 role=watch 的判斷，說明：
   - 哪些面向證據不足或品質存疑
   - 訊號矛盾的面向及可能解釋
   - 來源可信度的已知限制
   - 建議後續觀察的指標或時間點
7. 證據矛盾時說明採信哪一側的邏輯：比較時效性、來源獨立性、與直接性。
8. 不得把媒體或第三方分析師的結論直接當成系統結論。
9. null 或缺失資料不得補值；來源截止日與即時資料必須分開表達。
10. 禁止保證式預測與買賣建議。
"""
SENTIMENT_LABEL_SYSTEM = """\
你要為加密貨幣相關文本評估**輿論傾向** —— 也就是這段文字的語氣與措辭
透露出撰稿者／市場當下**怎麼看**這件事。

對每一則文本給一個 -1 到 1 之間的分數：
- 接近 +1：語氣樂觀、興奮、看好
- 接近 0：中性陳述、純資訊、或多空並陳
- 接近 -1：語氣悲觀、恐慌、看衰

**評估的是文字的語氣，不是事件的實質影響。**
一則利多事件也可能被以懷疑的語氣報導，那應該給偏低的分數。
回傳的分數數量必須與輸入的文本數量完全相同，且順序一致。
"""

FUNDAMENTAL_LABEL_SYSTEM = """\
你要為加密貨幣相關文本評估**事件的實質影響** —— 也就是撇開報導語氣，
這件事本身對該資產的基本面是正面還是負面。

對每一則文本給一個 -1 到 1 之間的分數：
- 接近 +1：實質利多（機構採用、監管放行、網路升級、資金持續流入、供給收緊）
- 接近 0：與該資產基本面無關、或影響不明
- 接近 -1：實質利空（駭客竊損、監管禁令、重大解鎖拋壓、鏈上活動萎縮、專案失敗）

**評估的是事件的後果，不是報導的語氣。**
一則以悲觀語氣寫成的報導，若描述的是利多事件，仍應給正分。
回傳的分數數量必須與輸入的文本數量完全相同，且順序一致。
"""

LABEL_SYSTEM = {
    LabelAspect.SENTIMENT: SENTIMENT_LABEL_SYSTEM,
    LabelAspect.FUNDAMENTAL: FUNDAMENTAL_LABEL_SYSTEM,
}


def plan_prompt(context: GatherContext) -> str:
    """把當下的蒐集狀態寫成給模型的敘述。"""
    lines = [f"分析標的：{context.asset.value}", f"分析題目：{context.question}", ""]
    # 時間錨點 —— 讓模型知道「現在幾點」以正確判斷證據新鮮度
    if context.analysis_timestamp:
        lines.append(f"本次分析時間（UTC）：{context.analysis_timestamp}")
        lines.append("")

    # 題型需求先講 —— 它決定「什麼算蒐集完成」，是這一輪規劃的框架。
    if context.requirement_brief:
        lines.append("── 本題的證據需求 ──")
        lines.append(context.requirement_brief)
        lines.append("")

    if context.gap_brief:
        lines.append("── 目前的證據缺口（由系統規則判定，非你自行判斷）──")
        lines.append(context.gap_brief)
        lines.append("")
        lines.append("標示「必補」的缺口未關閉前，蒐集迴圈不會結束。請針對它們選擇工具。")
        lines.append("")

    gap = "、".join(sorted(facet.value for facet in context.gap)) or "無"
    lines.append(f"目前仍有缺口的證據面：{gap}")
    if context.gap_state is not None:
        state = context.gap_state
        lines.append(f"正反方向皆有證據：{'是' if state.direction_balance else '否'}")
        contradictions = "、".join(sorted(f.value for f in state.contradiction_facets)) or "無"
        lines.append(f"存在矛盾訊號的面向：{contradictions}")
        lines.append(f"獨立來源數：{state.independent_sources}（最低要求 2）")
        lines.append(f"證據時效性合格：{'是' if state.fresh else '否'}")
        lines.append(f"尚待處理的品質缺口：{', '.join(state.reasons) or '無'}")

    if context.evidence:
        lines.append("")
        lines.append(f"已蒐集到 {len(context.evidence)} 項證據：")
        for facet in Facet:
            items = [item for item in context.evidence if item.facet is facet]
            if items:
                lines.append(f"  - {facet.value}：{len(items)} 項")
                for item in items[:5]:
                    lines.append(
                        f"      [{item.id}] direction={item.stance_hint:+.2f} "
                        f"summary={item.summary}"
                    )
    else:
        lines.append("")
        lines.append("目前尚未蒐集到任何證據。")

    if context.attempts:
        lines.append("")
        lines.append("先前已嘗試過的工具呼叫：")
        for attempt in context.attempts:
            lines.append(f"  - {attempt.tool}({attempt.arguments}) → {attempt.outcome}")
        lines.append("")
        lines.append("已經回傳過空結果的工具不要用相同參數重試。")

    return "\n".join(lines)


def synthesis_prompt(
    asset: Asset, evidence: tuple[Evidence, ...], question: str = "請分析當前市場狀況"
) -> str:
    """把證據清單寫成給模型的敘述，含每項證據的 ID 與原文。"""
    lines = [f"分析標的：{asset.value}", f"分析題目：{question}", "", "可用證據："]

    for item in evidence:
        lines.append("")
        lines.append(f"[{item.id}]（{item.facet.value}）{item.summary}")
        for excerpt in item.excerpts:
            lines.append(f"    原文：{excerpt.text}")
            lines.append(f"    出處：{excerpt.url}")
            lines.append(f"    取得時間：{excerpt.retrieved_at.isoformat()}")


    lines.append("")
    lines.append("── 證據品質摘要 ──")
    facet_counts: dict[str, int] = {}
    source_ids: set[str] = set()
    for item in evidence:
        facet_counts[item.facet.value] = facet_counts.get(item.facet.value, 0) + 1
        for excerpt in item.excerpts:
            source_ids.add(excerpt.source_id)
    lines.append(f"各面證據數：{facet_counts}")
    lines.append(f"獨立來源數：{len(source_ids)}")

    positive = sum(1 for item in evidence if item.stance_hint > 0.15)
    negative = sum(1 for item in evidence if item.stance_hint < -0.15)
    neutral_count = len(evidence) - positive - negative
    lines.append(f"方向分布：偏多 {positive} / 中性 {neutral_count} / 偏空 {negative}")
    if positive > 0 and negative == 0:
        lines.append("⚠️ 警告：目前缺乏反方證據，你必須在 role=risk 中明確指出此限制。")
    elif negative > 0 and positive == 0:
        lines.append("⚠️ 警告：目前缺乏正方證據，你必須在 role=risk 中明確指出此限制。")

    lines.append("")
    lines.append("── 輸出要求 ──")
    lines.append("請根據以上證據寫出判斷。每則判斷的 evidence_ids 只能使用上方出現過的 ID。")
    lines.append(
        "必須包含：至少 1 個 fact、1 個 inference、1 個 conclusion、"
        "1 個 counter_evidence 或 risk、1 個 watch。"
    )
    return "\n".join(lines)


__all__ = [
    "FUNDAMENTAL_LABEL_SYSTEM",
    "LABEL_SYSTEM",
    "PLAN_SYSTEM",
    "SENTIMENT_LABEL_SYSTEM",
    "SYNTHESIS_SYSTEM",
    "plan_prompt",
    "synthesis_prompt",
]
