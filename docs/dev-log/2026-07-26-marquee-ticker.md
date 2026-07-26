# 開發日誌：前端跑馬燈即時幣價功能

**日期**：2026-07-26  
**開發者**：AL  
**分支**：`feature/crypto-marquee-ticker`

## 本次進度

完成前端 top-bar 幣價顯示區域的跑馬燈改造。

## 改動內容

| 檔案 | 說明 |
|------|------|
| `frontend/src/app/api/v1/crypto-prices/route.ts` | 新增 — server-side 代理 API，抓取 Yahoo Finance 即時幣價 |
| `frontend/src/components/top-bar.tsx` | 修改 — ticker 區域改為 CSS marquee 跑馬燈 |
| `frontend/src/app/globals.css` | 修改 — 底部加入 marquee keyframe 動畫 |

## 功能說明

1. **即時幣價跑馬燈**：top-bar 原本硬編碼的三幣靜態數據，改為即時抓取五幣（BTC、ETH、SOL、BNB、XRP）報價
2. **資料來源**：Yahoo Finance v8 API，透過 Next.js API route 做 server-side proxy 避免 CORS
3. **自動更新**：跑馬燈滾完一輪後自動 fetch 最新幣價，再進入下一輪
4. **漲跌顏色**：漲幅紅色、跌幅綠色（台股慣例）
5. **互動**：hover 暫停跑馬燈；點擊幣種連結到 Yahoo 加密貨幣頁面

## 未動到的部分

- 後端 Python 程式碼完全未修改
- 前端其他元件（sidebar、chat、panels、views）未動
- 只影響 top-bar 的 ticker 顯示區域

## 下一步

- 等組員 code review 後合併至 main
- 可考慮加入 mobile 版跑馬燈（目前只在桌面版顯示）
