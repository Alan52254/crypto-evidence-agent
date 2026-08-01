"use client";

import { useState } from "react";
import { Download, Loader2, CheckCircle2, AlertCircle } from "lucide-react";

interface ExportZipButtonProps {
  runId: string;
  sessionId?: string;
}

/**
 * PDF report + evidence + trace + config ZIP download button.
 * Calls POST /api/v1/export-artifacts and triggers a browser download.
 */
export function ExportZipButton({ runId, sessionId }: ExportZipButtonProps) {
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");

  async function handleDownload() {
    setStatus("loading");
    setErrorMsg("");

    try {
      const response = await fetch("/api/v1/export-artifacts", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ run_id: runId, session_id: sessionId }),
      });

      if (!response.ok) {
        const errData = await response.json().catch(() => ({ error: "Export failed" }));
        throw new Error(errData.error || `HTTP ${response.status}`);
      }

      // Extract filename from Content-Disposition header
      const disposition = response.headers.get("Content-Disposition") ?? "";
      const filenameMatch = disposition.match(/filename="?([^";\n]+)"?/);
      const filename = filenameMatch?.[1] ?? `AlphaSonar_Analysis_${runId.slice(0, 8)}.zip`;

      // Trigger download
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      document.body.removeChild(anchor);
      URL.revokeObjectURL(url);

      setStatus("done");
      setTimeout(() => setStatus("idle"), 3000);
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : String(err));
      setStatus("error");
      setTimeout(() => setStatus("idle"), 5000);
    }
  }

  return (
    <div className="mt-4 border-t border-outline-variant pt-4">
      <button
        type="button"
        onClick={handleDownload}
        disabled={status === "loading"}
        className={`
          group flex w-full items-center gap-3 rounded-xl border px-4 py-3
          transition-all duration-200
          ${status === "error"
            ? "border-red-300 bg-red-50 hover:bg-red-100"
            : status === "done"
            ? "border-emerald-300 bg-emerald-50"
            : "border-outline-variant bg-surface-container-low hover:bg-surface-container hover:border-primary/30 hover:shadow-sm"
          }
          disabled:opacity-60 disabled:cursor-wait
        `}
      >
        {/* Icon */}
        <div
          className={`
            flex h-9 w-9 items-center justify-center rounded-lg
            ${status === "loading"
              ? "bg-primary/10"
              : status === "done"
              ? "bg-emerald-100"
              : status === "error"
              ? "bg-red-100"
              : "bg-primary/10 group-hover:bg-primary/20"
            }
          `}
        >
          {status === "loading" ? (
            <Loader2 className="h-4.5 w-4.5 animate-spin text-primary" />
          ) : status === "done" ? (
            <CheckCircle2 className="h-4.5 w-4.5 text-emerald-600" />
          ) : status === "error" ? (
            <AlertCircle className="h-4.5 w-4.5 text-red-600" />
          ) : (
            <Download className="h-4.5 w-4.5 text-primary" />
          )}
        </div>

        {/* Text */}
        <div className="flex flex-col items-start text-left">
          <span className="text-[13px] font-semibold text-primary">
            {status === "loading"
              ? "Generating PDF & packaging..."
              : status === "done"
              ? "Download complete!"
              : status === "error"
              ? "Export failed"
              : "Download PDF Report & Data Package (ZIP)"}
          </span>
          <span className="text-[11px] text-secondary">
            {status === "error"
              ? errorMsg
              : "PDF report + evidence JSON + execution trace + agent config"}
          </span>
        </div>
      </button>
    </div>
  );
}
