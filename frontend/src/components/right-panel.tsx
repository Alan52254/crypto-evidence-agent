"use client";

import { useRef, useEffect } from "react";
import {
  TerminalSquare,
  X,
  AlertTriangle,
  Cpu,
  Eye,
  Search,
  FileText,
  Zap,
  ExternalLink,
} from "lucide-react";
import type { EvidenceRecord, TraceStreamEvent, Facet } from "@/lib/contracts";

/* ─────────────────────── Props ─────────────────────── */

interface RightPanelProps {
  events: TraceStreamEvent[];
  remaining: number;
  running: boolean;
  activeEvidence: EvidenceRecord | null;
  onCloseEvidence: () => void;
}

/* ─────────────────────── Main ─────────────────────── */

export function RightPanel({
  events,
  remaining,
  running,
  activeEvidence,
  onCloseEvidence,
}: RightPanelProps) {
  return (
    <aside className="hidden w-[300px] flex-shrink-0 flex-col border-l border-outline-variant bg-surface xl:flex">
      {activeEvidence ? (
        <EvidenceViewer
          evidence={activeEvidence}
          onClose={onCloseEvidence}
        />
      ) : (
        <TracePanel
          events={events}
          remaining={remaining}
          running={running}
        />
      )}
    </aside>
  );
}

/* ─────────────── Trace Panel ─────────────── */

function TracePanel({
  events,
  remaining,
  running,
}: {
  events: TraceStreamEvent[];
  remaining: number;
  running: boolean;
}) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events]);

  const minutes = String(Math.floor(remaining / 60)).padStart(2, "0");
  const seconds = String(remaining % 60).padStart(2, "0");

  return (
    <div className="flex h-full flex-col">
      {/* Header — compact, single row */}
      <header className="flex h-11 flex-shrink-0 items-center justify-between border-b border-outline-variant px-4">
        <h2 className="flex items-center gap-2 text-[13px] font-semibold text-primary">
          <TerminalSquare className="h-4 w-4 text-on-primary-container" />
          ReAct Trace
        </h2>
        <div className="flex items-center gap-1.5">
          <span
            className={`tabular font-mono text-[13px] font-bold ${
              remaining < 60 ? "text-error" : "text-secondary"
            }`}
          >
            {minutes}:{seconds}
          </span>
          <span className="text-[9px] text-outline">/ 15:00</span>
        </div>
      </header>

      {/* Events list */}
      <div
        className="sidebar-scroll flex-1 overflow-y-auto px-3 py-3"
        aria-live="polite"
      >
        <div className="space-y-2.5">
          {events.map((event) => (
            <TraceEvent key={`${event.seq}-${event.kind}`} event={event} />
          ))}
          {!events.length && (
            <div className="flex flex-col items-center gap-2 py-16 text-center">
              <Cpu className="h-6 w-6 text-outline" />
              <p className="text-[12px] text-secondary">
                {running
                  ? "等待第一個 runtime event…"
                  : "尚未啟動分析"}
              </p>
            </div>
          )}
          <div ref={bottomRef} />
        </div>
      </div>

      {/* Footer: facet coverage */}
      {events.length > 0 && (
        <footer className="flex-shrink-0 border-t border-outline-variant px-4 py-2.5">
          <p className="mb-1 text-[10px] font-semibold uppercase tracking-widest text-secondary">
            Coverage
          </p>
          <div className="flex flex-wrap gap-1.5">
            {(
              ["technical", "positioning", "fundamental", "sentiment"] as Facet[]
            ).map((facet) => {
              const covered = events.some(
                (e) => e.kind === "gather" && e.evidence_ids.length > 0,
              );
              return (
                <span
                  key={facet}
                  className={`rounded-pill px-2 py-0.5 text-[10px] font-medium capitalize ${
                    covered
                      ? "bg-tertiary-fixed/20 text-on-tertiary-container"
                      : "bg-surface-container-high text-secondary"
                  }`}
                >
                  {facet}
                </span>
              );
            })}
          </div>
        </footer>
      )}
    </div>
  );
}

/* ─────────────── Trace Event ─────────────── */

function TraceEvent({ event }: { event: TraceStreamEvent }) {
  const isWarning =
    event.kind.includes("unavailable") ||
    event.kind.includes("budget") ||
    event.kind.includes("dropped");

  const isPlan =
    event.kind === "plan" || event.kind === "synthesise";

  const borderColor = isWarning
    ? "border-l-error"
    : isPlan
    ? "border-l-on-primary-container"
    : "border-l-on-tertiary-container";

  const labelColor = isWarning
    ? "text-error"
    : isPlan
    ? "text-on-primary-container"
    : "text-on-tertiary-container";

  const labelBg = isWarning
    ? "bg-error-container/40"
    : isPlan
    ? "bg-primary-fixed/40"
    : "bg-tertiary-fixed/20";

  const label = isWarning
    ? "WARNING"
    : event.kind === "plan"
    ? "THOUGHT"
    : event.kind === "gap_check"
    ? "GAP CHECK"
    : "OBSERVATION";

  const LabelIcon = isWarning
    ? AlertTriangle
    : isPlan
    ? Eye
    : event.kind === "gap_check"
    ? Search
    : Zap;

  return (
    <article
      className={`animate-slide-in-left rounded-r-lg border-l-2 bg-surface-container-lowest p-2.5 shadow-card ${borderColor}`}
    >
      <div className="flex items-center justify-between">
        <span
          className={`inline-flex items-center gap-1 rounded-pill px-1.5 py-px text-[9px] font-bold uppercase tracking-wider ${labelBg} ${labelColor}`}
        >
          <LabelIcon className="h-2.5 w-2.5" />
          {label}
        </span>
        <span className="tabular font-mono text-[9px] text-secondary">
          {event.elapsed_seconds.toFixed(2)}s
        </span>
      </div>

      <p className="mt-1.5 whitespace-pre-wrap text-[11px] leading-[16px] text-on-surface-variant">
        {event.reason}
      </p>

      {/* Execution records */}
      {event.executions.map((exec, i) => (
        <div
          key={`${exec.tool}-${exec.asset}-${i}`}
          className="mt-1.5 rounded-md border border-outline-variant bg-surface-container-low p-2"
        >
          <div className="flex items-center gap-1 font-mono text-[9px] font-semibold text-primary">
            <Zap className="h-2.5 w-2.5" />
            {exec.tool}{" "}
            <span className="rounded bg-surface-container-high px-1 py-px text-[8px] text-secondary">
              {exec.asset}
            </span>
          </div>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap font-mono text-[9px] leading-[14px] text-secondary">
            {JSON.stringify(exec.arguments, null, 2)}
          </pre>
          {exec.observation && (
            <p className="mt-1 text-[9px] text-on-tertiary-container">
              {exec.status} · {exec.observation}
            </p>
          )}
        </div>
      ))}

      {/* Gap state */}
      {event.gap?.reasons?.length > 0 && (
        <p className="mt-1.5 text-[9px] text-on-primary-container">
          GAP · {event.gap.reasons.join(" / ")}
        </p>
      )}
    </article>
  );
}

/* ─────────────── Evidence Viewer ─────────────── */

function EvidenceViewer({
  evidence,
  onClose,
}: {
  evidence: EvidenceRecord;
  onClose: () => void;
}) {
  return (
    <div className="flex h-full flex-col">
      {/* Header */}
      <header className="flex h-11 flex-shrink-0 items-center justify-between border-b border-outline-variant px-4">
        <div className="min-w-0 flex-1">
          <h2 className="flex items-center gap-1.5 text-[13px] font-semibold text-primary">
            <FileText className="h-4 w-4" />
            Evidence
          </h2>
        </div>
        <button
          onClick={onClose}
          className="ml-2 rounded-md p-1 text-secondary transition-colors hover:bg-surface-container-low hover:text-primary"
          aria-label="Close evidence viewer"
        >
          <X className="h-4 w-4" />
        </button>
      </header>

      {/* Content */}
      <div className="sidebar-scroll flex-1 overflow-y-auto px-4 py-3">
        <p className="mb-1 break-all font-mono text-[11px] text-secondary">
          {evidence.evidence_id}
        </p>
        <p className="text-[13px] leading-relaxed text-on-surface">
          {evidence.summary}
        </p>

        <dl className="my-3 grid grid-cols-2 gap-2 rounded-lg border border-outline-variant bg-surface-container-low p-2.5 text-[11px]">
          <div>
            <dt className="text-[9px] font-semibold uppercase tracking-wider text-secondary">
              Facet
            </dt>
            <dd className="mt-0.5 font-medium capitalize text-primary">
              {evidence.facet}
            </dd>
          </div>
          <div>
            <dt className="text-[9px] font-semibold uppercase tracking-wider text-secondary">
              Direction
            </dt>
            <dd className="mt-0.5 font-mono font-medium text-primary">
              {evidence.stance_hint.toFixed(2)}
            </dd>
          </div>
        </dl>

        <div className="space-y-2">
          <h3 className="flex items-center gap-1.5 text-[10px] font-semibold uppercase tracking-widest text-secondary">
            <FileText className="h-3 w-3" />
            Sources ({evidence.sources.length})
          </h3>
          {evidence.sources.map((source, index) => (
            <article
              key={`${source.source_url}-${index}`}
              className="rounded-lg border border-outline-variant bg-surface-container-lowest p-3 shadow-card"
            >
              <p className="font-mono text-[10px] font-medium text-primary">
                {source.source}
              </p>
              <p className="mt-1.5 text-[11px] leading-[16px] text-on-surface-variant">
                {source.content_reference.excerpt}
              </p>
              <p className="mt-1.5 text-[9px] text-secondary">
                {source.content_reference.locator} · {source.fetched_at}
              </p>
              <a
                className="mt-1 inline-flex items-center gap-1 text-[9px] text-primary underline transition-colors hover:text-primary/70"
                href={source.source_url}
                target="_blank"
                rel="noreferrer"
              >
                <ExternalLink className="h-2.5 w-2.5" />
                {source.source_url}
              </a>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
