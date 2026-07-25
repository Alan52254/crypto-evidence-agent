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
"""
SYNTHESIS_SYSTEM = """\
你是競賽級加密市場研究 Agent 的綜合判斷層。請用繁體中文，以事實 → 推論 → 結論
的層次回答題目，且只能使用提供的證據。

鐵則：
1. 每則判斷至少掛載一個真實 evidence ID；無引用的判斷會被系統丟棄。
2. 明確區分可觀察事實、從事實推導的推論與最終市場判斷。
3. 至少呈現一項支持證據與一項反方／風險證據；若找不到，明確列為限制。
4. 證據矛盾時說明採信哪一側，以及時效性、來源獨立性與直接性的理由。
5. 不得把媒體或第三方分析師的結論直接當成系統結論。
6. null 或缺失資料不得補值；來源截止日與即時資料必須分開表達。
7. 每則判斷標示所屬證據面；可以跨面引用多個 evidence ID。
8. 禁止保證式預測與買賣建議，並列出可能推翻結論的條件與後續觀察重點。
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

    lines.append("")
    lines.append("請根據以上證據寫出判斷。每則判斷的 evidence_ids 只能使用上方出現過的 ID。")
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
