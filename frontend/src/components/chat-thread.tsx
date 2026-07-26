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
  // Wrap consecutive <tr> in <table>
  html = html.replace(/(<tr>.*<\/tr>\n?)+/g, (match) => `<table>${match}</table>`);
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

const PROMPT_SUGGESTIONS: { text: string; asset: Asset }[] = [
  { text: "市場上認為 BTC 短期盤整，請驗證正反證據。", asset: "BTC" },
  { text: "Analyze ETH's on-chain metrics vs price action divergence.", asset: "ETH" },
  { text: "SOL 生態系近期資金流向有異常嗎？", asset: "SOL" },
  { text: "Compare BNB burn mechanism impact on quarterly returns.", asset: "BNB" },
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
        {!hasContent && <WelcomeState onSelectPrompt={onSelectPrompt} />}

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

/* ─────────────── Welcome / Empty State ─────────────── */

function WelcomeState({
  onSelectPrompt,
}: {
  onSelectPrompt?: (prompt: string, asset: Asset) => void;
}) {
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
        {PROMPT_SUGGESTIONS.map(({ text, asset }) => (
          <button
            key={text}
            type="button"
            onClick={() => onSelectPrompt?.(text, asset)}
            className="group rounded-2xl border border-outline-variant bg-surface-container-lowest p-4 text-left shadow-card transition-all duration-150 hover:border-primary hover:shadow-card-hover active:scale-[0.98]"
          >
            <div className="mb-1.5 flex items-center justify-between">
              <span className="rounded-pill bg-primary/10 px-2 py-0.5 font-mono text-[10px] font-bold text-primary">
                {asset}
              </span>
              <span className="text-[10px] text-secondary opacity-0 transition-opacity group-hover:opacity-100 font-semibold">
                點擊帶入 ↵
              </span>
            </div>
            <p className="text-[13px] leading-snug font-medium text-primary">
              {text}
            </p>
          </button>
        ))}
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
        <div className="mt-1 flex h-8 w-8 flex-shrink-0 items-center justify-center rounded-full bg-primary text-on-primary shadow-sm font-bold text-[12px]">
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
              [&_table]:w-full [&_table]:text-[12px] [&_table]:border-collapse
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
    </div>
  );
}
