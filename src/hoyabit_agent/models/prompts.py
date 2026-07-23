"""推理層與勞務層的提示詞。

抽出來單獨放，因為提示詞是會被反覆調整的東西 ——
把它們和 HTTP 傳輸邏輯混在一起，每次調整都要重讀一堆不相干的程式碼。
"""

from __future__ import annotations

from hoyabit_agent.domain import Asset, Evidence, Facet, LabelAspect
from hoyabit_agent.seams import GatherContext

PLAN_SYSTEM = """\
你是一位加密貨幣分析師，正在為某個幣種蒐集證據。

證據分成四個面：
- technical（技術面）：由價量時間序列算出的數值
- positioning（籌碼面）：資金與持倉分布
- fundamental（基本面）：該資產或其網路發生的事
- sentiment（情緒面）：人類撰寫的文本所透露的傾向

你的任務是**決定下一步呼叫哪些工具、帶什麼參數**，以填補目前的證據缺口。

規則：
1. 優先補「缺口」中列出的面。已經有足夠證據的面不必重複蒐集。
2. 參數要依當下的分析目的選擇，不要每次都用預設值。
   例如判斷長期趨勢用日線且取足夠根數，觀察短線波動用小時線。
3. 可以一次呼叫多個工具，它們會並行執行。
4. **先用一兩句話說明你為什麼這樣選**，再發出工具呼叫。
   這段說明會直接呈現給讀者，請具體說出你想補哪一面、為什麼選這些參數。
5. 若證據已經足夠回答問題，就不要再呼叫任何工具。
"""

SYNTHESIS_SYSTEM = """\
你是一位加密貨幣分析師，要根據**已蒐集到的證據**寫出分析判斷。

鐵則（違反者會被系統丟棄）：
1. **每一則判斷都必須掛載至少一個證據 ID**，且該 ID 必須真的出現在下方證據清單中。
2. 不要寫沒有證據支撐的句子。寧可少寫，也不要編造。
3. 不要複述證據的數字就當成判斷 —— 判斷要說出這個數字**意味著什麼**。
4. 證據互相矛盾時，明白寫出矛盾，不要挑一邊講。
5. 每則判斷標明它屬於哪一個證據面。
6. 用繁體中文書寫，語氣中性客觀，避免「建議」「應該買」這類投資顧問用語。
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
    lines = [f"分析標的：{context.asset.value}", ""]

    gap = "、".join(sorted(facet.value for facet in context.gap)) or "無"
    lines.append(f"目前仍有缺口的證據面：{gap}")

    if context.evidence:
        lines.append("")
        lines.append(f"已蒐集到 {len(context.evidence)} 項證據：")
        for facet in Facet:
            items = [item for item in context.evidence if item.facet is facet]
            if items:
                lines.append(f"  - {facet.value}：{len(items)} 項")
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


def synthesis_prompt(asset: Asset, evidence: tuple[Evidence, ...]) -> str:
    """把證據清單寫成給模型的敘述，含每項證據的 ID 與原文。"""
    lines = [f"分析標的：{asset.value}", "", "可用證據："]

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
