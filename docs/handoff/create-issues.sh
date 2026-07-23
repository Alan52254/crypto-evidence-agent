#!/usr/bin/env bash
# 一次開完 docs/handoff/ISSUES.md 裡的八個 issue。
#
# 前置：安裝 gh 並登入
#   https://cli.github.com/
#   gh auth login
#
# 用法：bash docs/handoff/create-issues.sh
#
# 這支腳本是冪等的反面 —— **重跑會開出重複的 issue**。只跑一次。

set -euo pipefail

REPO="${REPO:-Alan52254/crypto-evidence-agent}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

command -v gh >/dev/null || { echo "找不到 gh，先安裝：https://cli.github.com/"; exit 1; }

echo "將在 $REPO 開 7 個 issue。"
read -rp "確定要繼續嗎？(y/N) " reply
[[ "$reply" == "y" || "$reply" == "Y" ]] || { echo "已取消。"; exit 0; }

# 先建標籤（已存在就略過）
while IFS='|' read -r name colour; do
  gh label create "$name" --repo "$REPO" --color "$colour" 2>/dev/null || true
done <<'LABELS'
priority:high|d73a4a
priority:medium|fbca04
priority:low|0e8a16
time-sensitive|b60205
backend|1d76db
frontend|5319e7
quality|006b75
docs|0075ca
demo|c5def5
bug|d73a4a
LABELS

# 從 ISSUES.md 抽出每一則的內文（`## #N — 標題` 到下一個 `## #` 之間）
section() {
  awk -v n="$1" '
    $0 ~ "^## #" n " " { grab=1; next }
    grab && /^## #/    { exit }
    grab               { print }
  ' "$ROOT/docs/handoff/ISSUES.md" | sed '/^---$/d'
}

create() {
  local number="$1" title="$2" labels="$3"
  echo "  開 issue：$title"
  gh issue create --repo "$REPO" --title "$title" --label "$labels" \
    --body "$(section "$number")

---
完整驗收條件與交接說明見 \`docs/handoff/ISSUES.md\` 與 \`.scratch/analysis-agent/issues/\`。"
}

create 1 "軌跡視覺化前端（ticket 09）"              "priority:high,frontend"
create 2 "背景 ingestion 與向量檢索（ticket 08）"    "priority:high,backend,time-sensitive"
create 3 "評估基準與成績單（ticket 11）"             "priority:medium,quality"
create 4 "報告缺少分析涵蓋的時間窗"                   "priority:medium,bug"
create 5 "補上 CoinGecko 與自家聚合指標兩個證據源"    "priority:medium,backend"
create 6 "補上 .kiro/specs/ 架構規格"                "priority:low,docs"
create 7 "以 Kiro 載入 MCP server 並錄影"            "priority:low,demo"

echo "完成。"
