"use client";

import { useEffect, useRef } from "react";
import {
  BrainCircuit,
  SearchCheck,
  Sparkles,
  TrendingUp,
  TrendingDown,
  Minus,
  ExternalLink,
} from "lucide-react";
import { ExportZipButton } from "@/components/export-zip-button";
import type {
  AnalysisReport,
  Asset,
  EvidenceRecord,
  TraceStreamEvent,
} from "@/lib/contracts";

/* ─────────────────────── Props ─────────────────────── */

interface ChatThreadProps {
  asset: Asset;
  question: string;
  submittedQuestion?: string;
  report: AnalysisReport | null;
  events: TraceStreamEvent[];
  running: boolean;
  onEvidenceClick: (evidence: EvidenceRecord) => void;
  onSelectPrompt?: (prompt: string, asset: Asset) => void;
}

/* ─────────────────────── Helpers ─────────────────────── */

function markdownToHtml(md: string): string {
  // Simple markdown to HTML converter (no external deps)
  let html = md
    // Headers
    .replace(/^### (.+)$/gm, "<h3>$1</h3>")
    .replace(/^## (.+)$/gm, "<h2>$1</h2>")
    .replace(/^# (.+)$/gm, "<h1>$1</h1>")
    // Bold
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    // Italic
    .replace(/\*(.+?)\*/g, "<em>$1</em>")
    // Code
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    // Images (SVG data URIs)
    .replace(/!\[([^\]]*)\]\(([^)]+)\)/g, '<img alt="$1" src="$2" />')
    // Links
    .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>')
    // Horizontal rule
    .replace(/^---$/gm, "<hr />")
    // Blockquote
    .replace(/^> (.+)$/gm, "<blockquote>$1</blockquote>")
    // Unordered list items
    .replace(/^- (.+)$/gm, "<li>$1</li>")
    // Table rows
    .replace(/^\|(.+)\|$/gm, (match) => {
      const cells = match.split("|").filter(Boolean).map((c) => c.trim());
      if (cells.every((c) => /^[-:]+$/.test(c))) return ""; // separator row
      const tag = cells.length > 0 ? "td" : "td";
      return `<tr>${cells.map((c) => `<${tag}>${c}</${tag}>`).join("")}</tr>`;
    });

  // Wrap consecutive <li> in <ul>
  html = html.replace(/(<li>.*<\/li>\n?)+/g, (match) => `<ul>${match}</ul>`);
  // Wrap consecutive <tr> in <table>, and give the table its own horizontal
  // scroll container — a wide table must never push the report card layout
  // itself out of shape.
  html = html.replace(
    /(<tr>.*<\/tr>\n?)+/g,
    (match) => `<div class="table-scroll"><table>${match}</table></div>`,
  );
  // Paragraphs: wrap remaining lines
  html = html
    .split("\n")
    .map((line) => {
      const trimmed = line.trim();
      if (!trimmed) return "";
      if (trimmed.startsWith("<")) return trimmed;
      return `<p>${trimmed}</p>`;
    })
    .join("\n");

  return html;
}

/* ─────────────────────── Constants ─────────────────── */

const PROMPT_SUGGESTIONS: { title: string; subtitle: string; text: string; asset: Asset }[] = [
  { title: "📊 巨鯨與資金流向分析", subtitle: "適合短中期交易者", text: "分析過去 24 小時交易所儲備量、巨鯨轉移與期貨持倉，評估潛在砸盤風險或軋空機會。", asset: "Market" },
  { title: "📈 總體經濟與大盤連動", subtitle: "適合中長線戰略分析", text: "對比最新美股總經指標實際值與預期落差，分析未來一週大盤價格走勢的潛在衝擊。", asset: "Market" },
  { title: "🎯 生態系收益與基本面評估", subtitle: "適合 DeFi 質押與生態投資者", text: "追蹤公鏈活躍地址、DEX 交易量、平均 Gas 費與質押年化收益，評估鏈上健康度。", asset: "Market" },
  { title: "📰 新聞與情緒分數分析", subtitle: "適合捕捉市場突發事件", text: "聚合 12 小時內全球主流媒體新聞，利用大語言模型進行語意分析並抓出 3 大關鍵點。", asset: "Market" },
];

/* ─────────────────────── Main ─────────────────────── */

export function ChatThread({
  asset,
  question,
  submittedQuestion,
  report,
  events,
  running,
  onEvidenceClick,
  onSelectPrompt,
}: ChatThreadProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events, report, running, submittedQuestion]);

  const activeUserQuestion = submittedQuestion || (running ? question : "");
  const hasContent = running || report || events.length > 0 || Boolean(activeUserQuestion);

  return (
    <div className="chat-scroll flex-1 overflow-y-auto">
      <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-6 pb-44 md:px-6">
        {!hasContent && <WelcomeState asset={asset} onSelectPrompt={onSelectPrompt} />}

        {hasContent && (
          <>
            {/* User message bubble */}
            <UserBubble asset={asset} question={activeUserQuestion || question} />

            {/* AI thinking / response */}
            {running && !report && (
              <AIBubble>
                <ThinkingDots />
                {events.length > 0 && (
                  <div className="mt-3 border-t border-outline-variant pt-3">
                    <p className="text-[11px] font-medium text-secondary">
                      Agent 已完成 {events.length} 個步驟 ·{" "}
                      {events[events.length - 1]?.kind ?? ""}
                    </p>
                  </div>
                )}
              </AIBubble>
            )}

            {/* Completed report */}
            {report && (
              <AIBubble>
                <ReportResponse
                  report={report}
                  onEvidenceClick={onEvidenceClick}
                />
              </AIBubble>
            )}
          </>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}

/* ─── 幣種專屬提示詞 ─── */
const COIN_PROMPTS: Record<string, { title: string; text: string }[]> = {
  BTC: [
    { title: "📥 機構與資金流向", text: "請幫我撈取最新的美國比特幣現貨 ETF 每日淨流入/流出數據，並結合全網主要交易所的比特幣儲備量趨勢，分析目前機構資金是在持續累積鎖倉，還是有潛在的機構拋售賣壓湧現？" },
    { title: "📈 總體經濟與大盤連動", text: "近期美國聯準會公佈的最新貨幣政策與市場預期相比有何落差？請幫我分析全球流動性指標（全球 M2 貨幣供給量）目前的年增率走勢，對比特幣未來一到三個月的中長期牛熊基調有何影響？" },
    { title: "📊 衍生品與清算風險", text: "請幫我分析當前永續合約的持倉量、資金費率以及爆倉熱力圖。目前市場上的槓桿率是否過高？比特幣在短期內更有可能引發多頭連環爆倉回檔，還是軋空暴漲？" },
    { title: "🔗 鏈上巨鯨與聰明錢", text: "請幫我追蹤鏈上智慧錢與持有 1,000 枚以上 BTC 的巨鯨錢包地址動向。過去一週內，這些長期持有者主要是在進行交易所充值準備套現，還是在持續轉出至冷錢包囤幣？" },
    { title: "📰 突發事件與情緒得分", text: "請幫我聚合過去 12 小時內全球關於比特幣、美國加密清晰法案或重大監管事件的頭條新聞，利用大語言模型進行語意情緒得分計算，並指出目前市場情緒偏向看漲還看跌，以及最核心的 3 個關鍵風險點是什麼？" },
  ],
  ETH: [
    { title: "📥 ETF 資金流與機構吸籌", text: "請幫我撈取最新的美國以太坊現貨 ETF 每日淨流入/流出數據，並對比灰度信託的流出速度與黑石等機構的買入量，分析目前機構資金對 ETH 的真實淨增量與潛在的長線拋壓情況？" },
    { title: "🔥 燃燒機制與通縮狀態", text: "請幫我調取以太坊主網過去 24 小時至一週內的 ETH 即時燃燒量與區塊費數據。在 Layer 2（如 Base、Arbitrum）全面分流的現狀下，目前的燃燒速度是否足以讓 ETH 維持通貨緊縮狀態？" },
    { title: "🏦 質押收益與智慧錢鎖倉", text: "請分析當前以太坊全網的質押率、信標鏈的驗證者排隊情況，以及再質押協議（如 EigenLayer）的資金鎖倉量。目前智慧錢主要是在解質押提現，還是在持續鎖倉累積代幣？" },
    { title: "⚔️ 公鏈市佔與生態競爭力", text: "請幫我撈取以太坊（包含其 Layer 2 全生態）與主要競爭對手（如 Solana、Sui）的鏈上基本面橫向對比數據，包含每日活躍地址數、DEX 交易量及總鎖倉量，分析以太坊目前的生態市佔率是正在被蠶食還是穩固？" },
    { title: "🛠️ 技術升級與路線圖進度", text: "請幫我聚合以太坊核心開發者會議的最新記錄，分析下一個重大升級（如 Pectra 升級、帳戶抽象 EIP-7702）的最新測試網進度與預計主網上線時間，並解讀這些升級對提升以太坊性能與降低 L2 成本的實質影響是什麼？" },
  ],
  SOL: [
    { title: "📥 網路效能與基建升級", text: "請幫我追蹤 Solana 獨立驗證者客戶端 Firedancer 1.0 的最新主網部署進度，以及全新共識協議 Alpenglow 的測試數據。目前全網的實際交易吞吐量與交易確認延遲，是否已成功實現亞秒級的網速飛躍？" },
    { title: "📈 鏈上經濟基本面", text: "請幫我撈取 Solana 過去 24 小時至一週內的最新鏈上基本面數據，包含每日活躍地址數、DEX 即時交易量、以及全網協議產生的費用總額。目前 Solana 生態的鏈上實質購買力與活躍度是否有超越以太坊及其 L2 生態的趨勢？" },
    { title: "💰 穩定幣與機構採用", text: "請幫我分析 Solana 鏈上的穩定幣（如 USDC、PayPal USD 等）總供應量與市佔率變化。近期傳統金融巨頭在 Solana 生態的穩定幣支付、跨境結算或實物資產代幣化上有哪些最新落地進展？" },
    { title: "🔗 衍生品與清算風險", text: "請幫我分析當前 SOL 永續合約的持倉量、資金費率以及爆倉熱力圖。目前期貨市場上的多空槓桿比率如何？SOL 短期內更有可能引發多頭清算回檔，還是擊穿空頭防線的軋空暴漲？" },
    { title: "📉 解鎖砸盤與賣壓觀測", text: "請幫我追蹤 FTX 破產財產目前持有的 SOL 代幣剩餘鎖倉量與批次解鎖時程，並結合全網中心化交易所的 SOL 儲備量變化，評估未來一到三個月內市場是否存在潛在的結構性拋壓風險？" },
  ],
  BNB: [
    { title: "📥 新幣挖礦與預期收益", text: "請幫我評估幣安最新一期新幣挖礦（Launchpool）或 Megadrop 的代幣經濟學，並結合當前 BNB 的總鎖倉量與市場借貸利率，預估本次持有 BNB 參加挖礦的預期年化收益率及回本週期是多少？" },
    { title: "🔥 季度銷毀與通縮模型", text: "請幫我追蹤 BNB 最新一季的自動銷毀數據與 BNB Chain 鏈上的即時 Gas 費燃燒量。按照目前的銷毀速度與通縮模型，BNB 距離達成最終總供應量降至 1 億枚的長線目標還需要多少時間？" },
    { title: "🏦 監管法規與合規進度", text: "請幫我聚合過去一週內關於幣安在全球（特別是美國、歐洲與杜拜）的最新合規進展、罰金繳納、或合規監察報告。目前幣安面臨的全球監管法律風險是正在消退還是加劇？" },
    { title: "🔗 鏈上生態與總鎖倉量", text: "請幫我撈取 BNB Chain（包含 BSC 與 opBNB Layer 2）最新的鏈上基本面數據，包含每日活躍地址數、DEX 交易量、以及總鎖倉量。目前 BNB Chain 生態在 Meme 幣或 DeFi 創新上是否具備與 Solana 競爭的吸引力？" },
    { title: "📉 機構資金與 ETF 進度", text: "請幫我追蹤全球金融機構（如灰度等）在德拉瓦州或其他地區申請或註冊 Spot BNB ETF 法定信託的最新法律進度。目前市場對於 BNB 現貨 ETF 通過與機構資金流入的預期得分如何？" },
  ],
  XRP: [
    { title: "⚖️ 瑞波案最終判決與法理定調", text: "請幫我追蹤 Ripple 與美國 SEC 訴訟案的最新法律進展。目前法院對於 XRP 在二級市場銷售的非證券地位是否有最終法律定調？雙方的罰金和解或上訴程序目前進度如何？" },
    { title: "📥 全球現貨 ETF 申請與審批", text: "請幫我撈取全球主要金融機構（如 Bitwise、Canary Capital、Grayscale 等）申請或將信託轉換為 Spot XRP ETF 的最新進度。目前美國 SEC 的審批時間線為何？市場對通過預期的情緒得分如何？" },
    { title: "🏦 跨境支付與央行採用度", text: "請幫我分析 Ripple 公司最新的跨境支付網絡全球採用數據，特別是在亞洲與中東的進展。目前有多少家受監管的金融機構、銀行或央行數位貨幣項目正在實質使用 XRP 作為橋樑資產？" },
    { title: "🔓 託管帳戶釋放與賣壓觀測", text: "請幫我查詢 Ripple 官方託管帳戶最新的代幣解鎖釋放紀錄，以及官方隨後重新鎖倉的比例。結合全網主要交易所的 XRP 儲備量變化，評估未來一到三個月內市場是否存在結構性的機構拋壓？" },
    { title: "🔗 鏈上生態與技術升級進度", text: "請幫我追蹤 XRP Ledger 分散式帳本的最新技術升級，特別是兼容以太坊虛擬機（EVM 側鏈）與原生 AMM 機制的推進狀況。目前 XRPL 鏈上的每日活躍地址與 DeFi 總鎖倉量是否有突破性成長？" },
  ],
};

/* ─────────────── Welcome / Empty State ─────────────── */

function WelcomeState({
  asset,
  onSelectPrompt,
}: {
  asset: Asset;
  onSelectPrompt?: (prompt: string, asset: Asset) => void;
}) {
  const coinKey = asset !== "Market" ? asset : null;
  const coinPrompts = coinKey ? COIN_PROMPTS[coinKey] : null;

  // 幣種專屬模式
  if (coinPrompts) {
    const coinLabel = ({ BTC: "比特幣", ETH: "以太坊", SOL: "SOL", BNB: "BNB", XRP: "XRP" } as Record<string, string>)[coinKey!] ?? coinKey;
    return (
      <div className="flex flex-1 flex-col items-center justify-center py-16 animate-fade-in">
        <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/5 border border-outline-variant">
          <Sparkles className="h-8 w-8 text-primary" />
        </div>
        <h2 className="mb-2 text-headline-lg font-bold text-primary">
          {coinLabel} 深度分析
        </h2>
        <p className="mb-8 max-w-md text-center text-body-md text-secondary">
          選擇以下分析角度，AI Agent 將針對 {coinLabel} 進行專屬即時數據調取。
        </p>
        <div className="grid w-full max-w-lg grid-cols-1 gap-3">
          {coinPrompts.map(({ title, text }) => (
            <button
              key={title}
              type="button"
              onClick={() => onSelectPrompt?.(text, asset)}
              className="group rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 text-left shadow-card transition-all duration-150 hover:border-primary hover:shadow-card-hover active:scale-[0.98]"
            >
              <p className="text-[13px] leading-snug font-bold text-primary mb-1">
                {title}
              </p>
              <p className="text-[12px] leading-snug font-medium text-on-surface line-clamp-2">
                {text}
              </p>
              <span className="mt-2 block text-[10px] text-secondary opacity-0 transition-opacity group-hover:opacity-100 font-semibold">
                點擊帶入 ↵
              </span>
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={() => onSelectPrompt?.("", "Market")}
          className="mt-4 text-[11px] text-secondary hover:text-primary transition-colors"
        >
          ← 返回全市場分析
        </button>
      </div>
    );
  }

  // 全市場模式（Market）
  return (
    <div className="flex flex-1 flex-col items-center justify-center py-20 animate-fade-in">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-primary/5 border border-outline-variant">
        <Sparkles className="h-8 w-8 text-primary" />
      </div>
      <h2 className="mb-2 text-headline-lg font-bold text-primary">
        開始你的市場分析
      </h2>
      <p className="mb-8 max-w-md text-center text-body-md text-secondary">
        點擊下方範例問題或於底部輸入框發表研究題目，AI Agent 將自動取證並出具可稽核研報。
      </p>
      <div className="grid w-full max-w-lg grid-cols-1 gap-3 sm:grid-cols-2">
        {PROMPT_SUGGESTIONS.map(({ title, subtitle, text, asset: promptAsset }) => (
          <button
            key={text}
            type="button"
            onClick={() => onSelectPrompt?.(text, promptAsset)}
            className="group rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 text-left shadow-card transition-all duration-150 hover:border-primary hover:shadow-card-hover active:scale-[0.98]"
          >
            <p className="text-[13px] leading-snug font-bold text-primary mb-0.5">
              {title}
            </p>
            <p className="text-[10px] text-secondary mb-1.5">
              {subtitle}
            </p>
            <p className="text-[12px] leading-snug font-medium text-on-surface">
              {text}
            </p>
            <span className="mt-2 block text-[10px] text-secondary opacity-0 transition-opacity group-hover:opacity-100 font-semibold">
              點擊帶入 ↵
            </span>
          </button>
        ))}
      </div>

      {/* 第五卡片 — 單一幣種深度分析 */}
      <div className="mt-3 w-full max-w-lg rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 shadow-card">
        <p className="text-[13px] leading-snug font-bold text-primary mb-0.5">
          🔍 單一幣種深度分析
        </p>
        <p className="text-[10px] text-secondary mb-1.5">
          適合想要鎖定特定資產的投資者
        </p>
        <div className="flex flex-wrap gap-2">
          {([
            { value: "BTC" as Asset, label: "比特幣" },
            { value: "ETH" as Asset, label: "以太坊" },
            { value: "SOL" as Asset, label: "SOL" },
            { value: "BNB" as Asset, label: "BNB" },
            { value: "XRP" as Asset, label: "XRP" },
          ]).map(({ value, label }) => (
            <button
              key={value}
              type="button"
              onClick={() => onSelectPrompt?.("", value)}
              className="rounded-lg border border-outline-variant bg-surface-container-low px-3 py-1.5 font-mono text-[11px] font-bold text-primary transition-all duration-150 hover:border-primary hover:bg-primary/10 active:scale-95"
            >
              {label}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

/* ─────────────── User Bubble ─────────────── */

function UserBubble({ asset, question }: { asset: Asset; question: string }) {
  return (
    <div className="flex justify-end animate-fade-in">
      <div className="max-w-[85%] md:max-w-[70%]">
        <div className="rounded-t-2xl rounded-bl-2xl bg-surface-container-high px-4 py-3 text-body-md text-on-surface shadow-sm">
          <div className="mb-1.5 inline-block rounded-pill bg-primary/10 px-2 py-0.5 font-mono text-[11px] font-bold text-primary">
            {asset}
          </div>
          <p className="leading-relaxed font-medium">{question}</p>
        </div>
      </div>
    </div>
  );
}

/* ─────────────── AI Bubble ─────────────── */

function AIBubble({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex justify-start animate-fade-in">
      <div className="flex max-w-[92%] gap-3 md:max-w-[82%]">
        <div className="mt-1 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-accent text-on-accent shadow-sm font-bold text-[12px]">
          AI
        </div>
        <div className="min-w-0 flex-1">
          <div className="ai-card rounded-r-2xl rounded-bl-2xl border border-outline-variant border-l-primary border-l-4 bg-surface-container-lowest px-5 py-4">
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

/* ─────────────── Thinking Dots ─────────────── */

function ThinkingDots() {
  return (
    <div className="flex items-center gap-2 py-1">
      <div className="h-2 w-2 rounded-full bg-primary animate-pulse-dot dot-delay-1" />
      <div className="h-2 w-2 rounded-full bg-primary animate-pulse-dot dot-delay-2" />
      <div className="h-2 w-2 rounded-full bg-primary animate-pulse-dot dot-delay-3" />
      <span className="ml-2 font-mono text-[12px] font-medium text-secondary">
        ReAct Agent 正在規劃與抓取數據...
      </span>
    </div>
  );
}

/* ─────────────── Report Response ─────────────── */

function ReportResponse({
  report,
  onEvidenceClick,
}: {
  report: AnalysisReport;
  onEvidenceClick: (evidence: EvidenceRecord) => void;
}) {
  const StanceIcon =
    report.stance === "bullish"
      ? TrendingUp
      : report.stance === "bearish"
      ? TrendingDown
      : Minus;

  const stanceColor =
    report.stance === "bullish"
      ? "text-emerald-700 bg-emerald-50 border-emerald-200"
      : report.stance === "bearish"
      ? "text-red-700 bg-red-50 border-red-200"
      : "text-amber-800 bg-amber-50 border-amber-200";

  return (
    <div className="space-y-4">
      {/* Header metrics */}
      <div className="flex flex-wrap items-center gap-3">
        <div
          className={`inline-flex items-center gap-1.5 rounded-pill border px-3 py-1 text-label-caps font-bold ${stanceColor}`}
        >
          <StanceIcon className="h-3.5 w-3.5" />
          {report.stance.toUpperCase()}
        </div>
        <div className="flex items-center gap-4 text-data-tabular font-mono text-xs">
          <span className="text-secondary">
            Confidence:{" "}
            <strong className="tabular font-mono text-primary font-bold">
              {report.confidence != null
                ? `${Math.round(report.confidence * 100)}%`
                : "N/A"}
            </strong>
          </span>
          <span className="text-secondary">
            Cutoff:{" "}
            <strong className="font-mono text-primary font-bold">
              {report.cutoff}
            </strong>
          </span>
        </div>
      </div>

      {/* Question */}
      <p className="text-body-md font-bold text-primary leading-relaxed">
        {report.question}
      </p>

      {/* Claims */}
      {report.claims.length > 0 && (
        <div className="space-y-2.5 border-t border-outline-variant pt-4">
          <h3 className="flex items-center gap-2 text-label-caps font-semibold uppercase text-secondary">
            <SearchCheck className="h-3.5 w-3.5" />
            Auditable Claims ({report.claims.length})
          </h3>
          {report.claims.map((claim, index) => (
            <article
              key={`${claim.text}-${index}`}
              className="rounded-xl border border-outline-variant bg-surface-container-low p-4 transition-colors hover:bg-surface-container"
            >
              <div className="mb-2 flex items-center justify-between">
                <span
                  className={`rounded-pill px-2.5 py-0.5 text-[10px] font-bold uppercase tracking-wider ${
                    claim.role === "counter_evidence" || claim.role === "risk"
                      ? "bg-red-100 text-red-700"
                      : claim.role === "watch"
                      ? "bg-amber-100 text-amber-800"
                      : "bg-emerald-100 text-emerald-700"
                  }`}
                >
                  {claim.role.replace("_", " ")}
                </span>
                <span className="text-[10px] text-secondary font-mono capitalize">
                  {claim.facet}
                </span>
              </div>
              <p className="text-body-md leading-relaxed text-primary font-medium">
                {claim.text}
              </p>
              <div className="mt-2.5 flex flex-wrap gap-1.5">
                {claim.evidence_ids.map((id) => {
                  const evidence = report.evidence.find(
                    (e) => e.evidence_id === id,
                  );
                  return (
                    <button
                      key={id}
                      type="button"
                      disabled={!evidence}
                      onClick={() => evidence && onEvidenceClick(evidence)}
                      className="inline-flex items-center gap-1 rounded-md border border-primary/20 bg-surface-container-lowest px-2 py-0.5 font-mono text-[10px] font-semibold text-primary transition-colors hover:bg-primary hover:text-on-primary disabled:opacity-40"
                    >
                      <ExternalLink className="h-2.5 w-2.5" />
                      {id}
                    </button>
                  );
                })}
              </div>
            </article>
          ))}
        </div>
      )}

      {/* Sources footer */}
      {report.evidence.length > 0 && (
        <div className="border-t border-outline-variant pt-3">
          <p className="text-[11px] font-mono text-secondary">
            📎 {report.evidence.length} evidence items from{" "}
            {new Set(
              report.evidence.flatMap((e) => e.sources.map((s) => s.source)),
            ).size}{" "}
            sources
          </p>
        </div>
      )}

      {/* Enhanced Report (Markdown with charts + investor insights) */}
      {report.enhanced_report_md && (
        <details className="border-t border-outline-variant pt-4 mt-4">
          <summary className="cursor-pointer text-label-caps font-semibold text-primary hover:text-primary/80 transition-colors">
            📊 展開完整增強報告（含圖表 + 投資者洞察 + 風險分析）
          </summary>
          <div
            className="mt-3 prose prose-sm max-w-none text-on-surface-variant leading-relaxed 
              [&_h1]:text-headline-lg [&_h1]:font-bold [&_h1]:text-primary [&_h1]:mt-6 [&_h1]:mb-3
              [&_h2]:text-headline-md [&_h2]:font-bold [&_h2]:text-primary [&_h2]:mt-5 [&_h2]:mb-2
              [&_h3]:text-body-lg [&_h3]:font-semibold [&_h3]:text-primary [&_h3]:mt-4 [&_h3]:mb-1
              [&_.table-scroll]:overflow-x-auto [&_.table-scroll]:max-w-full [&_.table-scroll]:-mx-1 [&_.table-scroll]:px-1
              [&_table]:w-full [&_table]:min-w-[420px] [&_table]:text-[12px] [&_table]:border-collapse
              [&_th]:border [&_th]:border-outline-variant [&_th]:bg-surface-container-low [&_th]:px-3 [&_th]:py-1.5 [&_th]:text-left [&_th]:font-semibold
              [&_td]:border [&_td]:border-outline-variant [&_td]:px-3 [&_td]:py-1.5
              [&_ul]:list-disc [&_ul]:pl-5 [&_ul]:space-y-1
              [&_li]:text-body-md
              [&_strong]:text-primary [&_strong]:font-semibold
              [&_code]:bg-surface-container-low [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:rounded [&_code]:text-[11px] [&_code]:font-mono
              [&_blockquote]:border-l-4 [&_blockquote]:border-l-primary [&_blockquote]:pl-4 [&_blockquote]:italic [&_blockquote]:text-secondary
              [&_hr]:border-outline-variant [&_hr]:my-4
              [&_img]:max-w-full [&_img]:rounded-lg [&_img]:my-3
              [&_p]:text-body-md [&_p]:leading-relaxed [&_p]:my-2"
            dangerouslySetInnerHTML={{ __html: markdownToHtml(report.enhanced_report_md) }}
          />
        </details>
      )}

      {/* ZIP Download Button */}
      <ExportZipButton runId={report.run_id} />
    </div>
  );
}
