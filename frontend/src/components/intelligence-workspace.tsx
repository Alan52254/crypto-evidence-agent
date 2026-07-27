"use client";

import { FormEvent, useEffect, useRef, useState } from "react";
import type {
  AnalysisReport,
  Asset,
  EvidenceRecord,
  TraceStreamEvent,
} from "@/lib/contracts";
import { Sidebar, type NavTab } from "@/components/sidebar";
import { TopBar } from "@/components/top-bar";
import { ChatThread } from "@/components/chat-thread";
import { ChatInputBar } from "@/components/chat-input-bar";
import { RightPanel } from "@/components/right-panel";
import { HistoryView, type HistoryItem } from "@/components/views/history-view";
import { LibraryView } from "@/components/views/library-view";
import { ReportsView } from "@/components/views/reports-view";
import { SettingsView } from "@/components/views/settings-view";

const MAX_SECONDS = 900;

export function IntelligenceWorkspace() {
  /* ── Navigation State ── */
  const [activeTab, setActiveTab] = useState<NavTab>("workspace");

  /* ── Dark Mode State ── */
  const [darkMode, setDarkMode] = useState(false);

  useEffect(() => {
    const saved = localStorage.getItem("theme");
    if (saved === "dark" || (!saved && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
      setDarkMode(true);
      document.documentElement.classList.add("dark");
      document.documentElement.classList.remove("light");
    }
  }, []);

  function toggleTheme() {
    setDarkMode((prev) => {
      const next = !prev;
      if (next) {
        document.documentElement.classList.add("dark");
        document.documentElement.classList.remove("light");
        localStorage.setItem("theme", "dark");
      } else {
        document.documentElement.classList.remove("dark");
        document.documentElement.classList.add("light");
        localStorage.setItem("theme", "light");
      }
      return next;
    });
  }

  /* ── Accent Theme State (personalization) ──
     Orthogonal to light/dark mode — this only swaps the `--accent`/
     `--accent-2` CSS variables that a small set of brand-accent surfaces
     read (send button, AI avatar, active nav, logo mark). */
  const [accentTheme, setAccentThemeState] = useState<"classic" | "aurum">("classic");

  useEffect(() => {
    const saved = localStorage.getItem("accent-theme");
    if (saved === "aurum") {
      setAccentThemeState("aurum");
      document.documentElement.classList.add("theme-aurum");
    }
  }, []);

  function setAccentTheme(next: "classic" | "aurum") {
    setAccentThemeState(next);
    document.documentElement.classList.toggle("theme-aurum", next === "aurum");
    localStorage.setItem("accent-theme", next);
  }

  /* ── ReAct Agent State ── */
  const [asset, setAsset] = useState<Asset>("BTC");
  const [question, setQuestion] = useState(
    "市場上認為 BTC 短期盤整，請驗證正反證據。",
  );
  const [submittedQuestion, setSubmittedQuestion] = useState("");
  const [events, setEvents] = useState<TraceStreamEvent[]>([]);
  const [report, setReport] = useState<AnalysisReport | null>(null);
  const [activeEvidence, setActiveEvidence] =
    useState<EvidenceRecord | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState("");
  const [remaining, setRemaining] = useState(MAX_SECONDS);
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const streamRef = useRef<EventSource | null>(null);
  const reportRef = useRef<AnalysisReport | null>(null);

  useEffect(() => {
    reportRef.current = report;
  }, [report]);

  /* ── Timers ── */
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(
      () => setRemaining((v) => Math.max(0, v - 1)),
      1000,
    );
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => () => streamRef.current?.close(), []);

  /* ── Submit handler ── */
  async function submit(event: FormEvent) {
    event.preventDefault();
    const currentQuestion = question.trim();
    if (!currentQuestion) return;

    setRunning(true);
    setError("");
    setSubmittedQuestion(currentQuestion);
    setQuestion("");
    setEvents([]);
    setReport(null);
    setRemaining(MAX_SECONDS);
    setActiveEvidence(null);
    streamRef.current?.close();

    try {
      const response = await fetch("/api/v1/analyse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ asset, question: currentQuestion }),
      });

      if (!response.ok) {
        setRunning(false);
        setError(`無法建立分析：HTTP ${response.status} (請確認 Python 後端服務已啟動)`);
        return;
      }

      const started = (await response.json()) as { stream_url: string };
      const source = new EventSource(started.stream_url);
      streamRef.current = source;

      source.addEventListener("trace", (message) => {
        const traceNode = JSON.parse((message as MessageEvent).data) as TraceStreamEvent;
        setEvents((items) => [...items, traceNode]);
      });

      source.addEventListener("complete", (message) => {
        const completedReport = JSON.parse((message as MessageEvent).data) as AnalysisReport;
        setReport(completedReport);
        setRunning(false);
        source.close();
      });

      // Handle SSE "error" event type from backend (agent failure)
      source.addEventListener("error", (message) => {
        try {
          const errorData = JSON.parse((message as MessageEvent).data);
          setError(`Agent 錯誤：${errorData?.error ?? "未知錯誤"}`);
        } catch {
          // Native EventSource error (connection lost) — not a JSON message
        }
        setRunning(false);
        source.close();
      });

      // Handle native connection errors with reconnect
      let reconnectAttempts = 0;
      const maxReconnects = 3;
      source.onerror = () => {
        if (source.readyState === EventSource.CLOSED || reportRef.current) return;
        reconnectAttempts++;
        if (reconnectAttempts >= maxReconnects) {
          setRunning(false);
          setError("SSE 連線中斷且重試失敗，請確認後端服務正在運行。");
          source.close();
        }
        // EventSource auto-reconnects by default unless we close it
      };
    } catch (err) {
      setRunning(false);
      setError(`無法連線至 Agent 後端：${err instanceof Error ? err.message : String(err)}`);
    }
  }

  function handleNewAnalysis() {
    setActiveTab("workspace");
    setSubmittedQuestion("");
    setReport(null);
    setEvents([]);
    setError("");
    setRunning(false);
    setActiveEvidence(null);
    setRemaining(MAX_SECONDS);
    setQuestion("");
    streamRef.current?.close();
  }

  /* ── Handle Prompt Selection ── */
  function handleSelectPrompt(prompt: string, selectedAsset: Asset) {
    setQuestion(prompt);
    setAsset(selectedAsset);
  }

  /* ── Export Current Report ── */
  function handleExport() {
    if (!report) {
      // If no report, export the current trace events as JSON
      if (events.length === 0) return;
      const data = JSON.stringify({ asset, question: submittedQuestion, events }, null, 2);
      const blob = new Blob([data], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${asset}_trace_${new Date().toISOString().slice(0, 10)}.json`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }

    // Generate markdown report
    const stanceEmoji = report.stance === "bullish" ? "\u{1F4C8}" : report.stance === "bearish" ? "\u{1F4C9}" : "\u{2796}";
    const confidenceStr = report.confidence != null ? `${Math.round(report.confidence * 100)}%` : "N/A";

    let md = `# ${stanceEmoji} ${report.asset} 市場分析研報\n\n`;
    md += `> **Generated by HOYA BIT Alpha Intel Agent**\n\n---\n\n`;
    md += `## 基本資訊\n\n| 欄位 | 內容 |\n|------|------|\n`;
    md += `| Run ID | \`${report.run_id}\` |\n`;
    md += `| 標的 | ${report.asset} |\n`;
    md += `| 研究問題 | ${report.question} |\n`;
    md += `| 研判方向 | **${report.stance.toUpperCase()}** |\n`;
    md += `| 跨因子置信度 | **${confidenceStr}** |\n`;
    md += `| 數據截止日 | ${report.cutoff} |\n\n`;

    if (report.facet_stances && Object.keys(report.facet_stances).length > 0) {
      md += `## 各因子方向判定\n\n| 因子 | 方向 |\n|------|------|\n`;
      for (const [facet, stance] of Object.entries(report.facet_stances)) {
        md += `| ${facet} | ${(stance as string).toUpperCase()} |\n`;
      }
      md += `\n`;
    }

    if (report.claims.length > 0) {
      md += `## 核心判斷條目\n\n`;
      report.claims.forEach((claim, i) => {
        md += `### ${i + 1}. [${claim.role.toUpperCase()}] ${claim.facet}\n\n${claim.text}\n\n`;
        if (claim.evidence_ids.length > 0) {
          md += `**引證**: ${claim.evidence_ids.map((id) => `\`${id}\``).join(", ")}\n\n`;
        }
      });
    }

    if (report.evidence.length > 0) {
      md += `## 證據清單\n\n`;
      report.evidence.forEach((ev) => {
        md += `### ${ev.evidence_id}\n- **面向**: ${ev.facet}\n- **摘要**: ${ev.summary}\n\n`;
      });
    }

    md += `---\n*本報告由 HOYA BIT ReAct Agent 自動產生，僅供參考，不構成投資建議。*\n`;

    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${report.asset}_report_${report.run_id.slice(0, 8)}.md`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }

  /* ── Load Historical Run ── */
  async function handleLoadRun(item: HistoryItem) {
    setAsset(item.asset);
    setQuestion(item.question);
    setSubmittedQuestion(item.question);
    setError("");
    setRunning(false);
    setActiveEvidence(null);

    // Try fetching real outcome payload from API first
    try {
      const res = await fetch(`/api/v1/runs/${item.id}`);
      if (res.ok) {
        const payload = await res.json();
        if (payload.report) {
          setReport(payload.report);
        } else {
          setReport(item.report);
        }
        if (payload.trace?.nodes) {
          setEvents(payload.trace.nodes);
        } else {
          setEvents(item.events);
        }
      } else {
        setReport(item.report);
        setEvents(item.events);
      }
    } catch {
      setReport(item.report);
      setEvents(item.events);
    }

    setActiveTab("workspace");
  }

  /* ── Render Active Main View ── */
  const renderMainView = () => {
    switch (activeTab) {
      case "history":
        return <HistoryView onLoadRun={handleLoadRun} />;
      case "library":
        return <LibraryView />;
      case "reports":
        return <ReportsView />;
      case "settings":
        return (
          <SettingsView
            accentTheme={accentTheme}
            onAccentThemeChange={setAccentTheme}
          />
        );
      case "workspace":
      default:
        return (
          <div className="flex min-h-0 flex-1">
            {/* Main Chat Thread Area */}
            <main className="relative flex min-w-0 flex-1 flex-col overflow-hidden">
              {/* Error banner */}
              {error && (
                <div className="mx-4 mt-3 rounded-lg border border-error/30 bg-error-container/30 px-4 py-2.5 text-[13px] text-error">
                  {error}
                </div>
              )}

              <ChatThread
                asset={asset}
                question={question}
                submittedQuestion={submittedQuestion}
                report={report}
                events={events}
                running={running}
                onEvidenceClick={setActiveEvidence}
                onSelectPrompt={handleSelectPrompt}
              />

              <ChatInputBar
                asset={asset}
                question={question}
                running={running}
                onAssetChange={setAsset}
                onQuestionChange={setQuestion}
                onSubmit={submit}
              />
            </main>

            {/* Right Panel for ReAct Agent Trace */}
            <RightPanel
              events={events}
              remaining={remaining}
              running={running}
              activeEvidence={activeEvidence}
              onCloseEvidence={() => setActiveEvidence(null)}
            />
          </div>
        );
    }
  };

  return (
    <div className="flex h-dvh bg-background text-on-surface">
      {/* Left Sidebar */}
      <Sidebar
        open={sidebarOpen}
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        onClose={() => setSidebarOpen(false)}
        onNewAnalysis={handleNewAnalysis}
      />

      {/* Right Content Area */}
      <div className="flex min-w-0 flex-1 flex-col">
        {/* Top bar — spans across full content width */}
        <TopBar
          onMenuToggle={() => setSidebarOpen((v) => !v)}
          remaining={remaining}
          running={running}
          onExport={handleExport}
          onThemeToggle={toggleTheme}
          darkMode={darkMode}
        />

        {/* Below top bar: active view */}
        {renderMainView()}
      </div>
    </div>
  );
}
