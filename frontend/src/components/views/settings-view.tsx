"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Settings as SettingsIcon,
  ShieldCheck,
  Zap,
  Lock,
  CheckCircle2,
  XCircle,
  RefreshCw,
  Loader2,
  Server,
  Activity,
  Palette,
} from "lucide-react";
import { Button } from "@/components/ui/button";

interface HealthStatus {
  frontend: string;
  backend: string;
  backend_url: string;
  timestamp: string;
}

type AccentTheme = "classic" | "aurum";

interface SettingsViewProps {
  accentTheme?: AccentTheme;
  onAccentThemeChange?: (theme: AccentTheme) => void;
}

export function SettingsView({
  accentTheme = "classic",
  onAccentThemeChange,
}: SettingsViewProps) {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(true);

  const checkHealth = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/health");
      if (res.ok) {
        const data = await res.json();
        setHealth(data);
      } else {
        setHealth({
          frontend: "ok",
          backend: "unreachable",
          backend_url: "http://127.0.0.1:8000",
          timestamp: new Date().toISOString(),
        });
      }
    } catch {
      setHealth({
        frontend: "ok",
        backend: "unreachable",
        backend_url: "http://127.0.0.1:8000",
        timestamp: new Date().toISOString(),
      });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkHealth();
  }, [checkHealth]);

  const backendOk = health?.backend === "ok";

  return (
    <div className="chat-scroll flex-1 overflow-y-auto p-6 md:p-8">
      <div className="mx-auto max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-1 border-b border-outline-variant pb-5">
          <div className="flex items-center gap-2 text-primary font-bold text-headline-lg">
            <SettingsIcon className="h-6 w-6 text-secondary" />
            <h2>System Settings & Risk Controls</h2>
          </div>
          <p className="text-body-md text-secondary">
            AIOps 系統維運、大模型路由容錯與 HOYA BIT 金融風控配置。
          </p>
        </div>

        {/* Section 0: System Health */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="flex items-center justify-between border-b border-outline-variant pb-3">
            <div className="flex items-center gap-2">
              <Server className="h-5 w-5 text-primary" />
              <h3 className="font-bold text-headline-md text-primary">
                系統連線狀態 (Live Health Check)
              </h3>
            </div>
            <button
              onClick={checkHealth}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-outline-variant px-3 py-1.5 text-xs text-secondary hover:bg-surface-container-low hover:text-primary transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              重新檢查
            </button>
          </div>

          {loading && (
            <div className="flex items-center gap-2 py-4">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
              <span className="text-body-md text-secondary">正在檢查系統狀態...</span>
            </div>
          )}

          {!loading && health && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {/* Frontend status */}
              <div className="rounded-xl border border-emerald-200 bg-emerald-50/30 p-4">
                <div className="flex items-center gap-2 mb-2">
                  <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                  <span className="font-semibold text-primary text-[13px]">Next.js Frontend</span>
                </div>
                <p className="font-mono text-[12px] text-emerald-700 font-bold">ONLINE</p>
                <p className="text-[11px] text-secondary mt-1">Port 3000 (開發模式)</p>
              </div>

              {/* Backend status */}
              <div className={`rounded-xl border p-4 ${backendOk ? "border-emerald-200 bg-emerald-50/30" : "border-red-200 bg-red-50/30"}`}>
                <div className="flex items-center gap-2 mb-2">
                  {backendOk
                    ? <CheckCircle2 className="h-5 w-5 text-emerald-600" />
                    : <XCircle className="h-5 w-5 text-red-500" />}
                  <span className="font-semibold text-primary text-[13px]">Python Agent Backend</span>
                </div>
                <p className={`font-mono text-[12px] font-bold ${backendOk ? "text-emerald-700" : "text-red-600"}`}>
                  {backendOk ? "ONLINE" : "OFFLINE / UNREACHABLE"}
                </p>
                <p className="text-[11px] text-secondary mt-1">{health.backend_url}</p>
              </div>

              {/* Timestamp */}
              <div className="md:col-span-2 rounded-xl border border-outline-variant bg-surface-container-low p-3 flex items-center justify-between">
                <span className="text-[11px] text-secondary">
                  最後檢查: {new Date(health.timestamp).toLocaleString("zh-TW")}
                </span>
                {!backendOk && (
                  <span className="text-[11px] text-red-600 font-medium">
                    請確認 Python 後端已啟動: <code className="font-mono bg-red-50 px-1.5 py-0.5 rounded">python -m hoyabit_agent.viz.server</code>
                  </span>
                )}
              </div>
            </div>
          )}
        </section>

        {/* Section 0.5: Appearance / Accent Theme */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="border-b border-outline-variant pb-3">
            <div className="flex items-center gap-2">
              <Palette className="h-5 w-5 text-primary" />
              <h3 className="font-bold text-headline-md text-primary">
                外觀 · 個人化主題
              </h3>
            </div>
            <p className="text-body-md text-secondary mt-1">
              選擇介面的強調色主題。不影響亮 / 暗色模式，兩者可自由組合。
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => onAccentThemeChange?.("classic")}
              className={`rounded-xl border-[1.5px] p-4 text-left transition-colors ${
                accentTheme === "classic"
                  ? "border-primary"
                  : "border-outline-variant hover:border-outline"
              }`}
            >
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-[12.5px] font-bold text-primary">經典黑白</span>
                <span
                  className={`h-4 w-4 rounded-full border-2 relative ${
                    accentTheme === "classic" ? "border-primary" : "border-outline-variant"
                  }`}
                >
                  {accentTheme === "classic" && (
                    <span className="absolute inset-[2px] rounded-full bg-primary" />
                  )}
                </span>
              </div>
              <div className="flex gap-1">
                <span className="h-4 w-4 rounded bg-on-surface" />
                <span className="h-4 w-4 rounded bg-secondary" />
                <span className="h-4 w-4 rounded border border-outline-variant" />
              </div>
            </button>

            <button
              type="button"
              onClick={() => onAccentThemeChange?.("aurum")}
              className={`rounded-xl border-[1.5px] p-4 text-left transition-colors ${
                accentTheme === "aurum"
                  ? "border-accent"
                  : "border-outline-variant hover:border-outline"
              }`}
            >
              <div className="flex items-center justify-between mb-2.5">
                <span className="text-[12.5px] font-bold text-primary">Aurum 金紫</span>
                <span
                  className={`h-4 w-4 rounded-full border-2 relative ${
                    accentTheme === "aurum" ? "border-accent" : "border-outline-variant"
                  }`}
                >
                  {accentTheme === "aurum" && (
                    <span className="absolute inset-[2px] rounded-full bg-accent" />
                  )}
                </span>
              </div>
              <div className="flex gap-1">
                <span className="h-4 w-4 rounded" style={{ background: "#F5B93D" }} />
                <span className="h-4 w-4 rounded" style={{ background: "#A855F7" }} />
                <span className="h-4 w-4 rounded" style={{ background: "#F5B93D66" }} />
              </div>
            </button>
          </div>
        </section>

        {/* Section 1: Security & Injection Defense */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="flex items-center gap-2 border-b border-outline-variant pb-3">
            <ShieldCheck className="h-5 w-5 text-emerald-600" />
            <h3 className="font-bold text-headline-md text-primary">
              Prompt Injection 提示詞注入防禦機制
            </h3>
          </div>
          <div className="space-y-3 text-body-md text-secondary">
            <div className="flex items-start gap-3">
              <CheckCircle2 className="h-5 w-5 text-emerald-600 mt-0.5 flex-shrink-0" />
              <div>
                <strong className="text-primary block">過濾器 (Sanitizer) 狀態: 啟用中 (ACTIVE)</strong>
                <p className="text-body-md text-secondary">
                  自動過濾越獄關鍵字 (<code className="font-mono text-xs bg-surface-container px-1 py-0.5 rounded">ignore previous instructions</code>, <code className="font-mono text-xs bg-surface-container px-1 py-0.5 rounded">override rules</code>, <code className="font-mono text-xs bg-surface-container px-1 py-0.5 rounded">jailbreak</code>)。
                </p>
              </div>
            </div>
            <div className="flex items-start gap-3">
              <CheckCircle2 className="h-5 w-5 text-emerald-600 mt-0.5 flex-shrink-0" />
              <div>
                <strong className="text-primary block">三明治 Prompt 結構 (Sandwich Prompting)</strong>
                <p className="text-body-md text-secondary">
                  使用者輸入經雙重隔離層包覆，防止任何外部指令改寫 Agent 核心規則。
                </p>
              </div>
            </div>
          </div>
        </section>

        {/* Section 2: Model & Resilience Routing */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="flex items-center gap-2 border-b border-outline-variant pb-3">
            <Zap className="h-5 w-5 text-primary" />
            <h3 className="font-bold text-headline-md text-primary">
              LLM 大模型路由與 Timeout 超時處置
            </h3>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="rounded-xl border border-outline-variant p-4">
              <span className="text-label-caps text-secondary uppercase font-semibold">首選模型</span>
              <p className="font-mono font-bold text-primary mt-1">Google Gemini 2.5 Flash</p>
            </div>
            <div className="rounded-xl border border-outline-variant p-4">
              <span className="text-label-caps text-secondary uppercase font-semibold">ReAct 最大超時限制</span>
              <p className="font-mono font-bold text-primary mt-1">15 分鐘 (900 Seconds)</p>
            </div>
            <div className="rounded-xl border border-outline-variant p-4">
              <span className="text-label-caps text-secondary uppercase font-semibold">單一工具 IO Timeout</span>
              <p className="font-mono font-bold text-primary mt-1">15 秒</p>
            </div>
            <div className="rounded-xl border border-outline-variant p-4">
              <span className="text-label-caps text-secondary uppercase font-semibold">最大迭代次數</span>
              <p className="font-mono font-bold text-primary mt-1">6 Iterations</p>
            </div>
          </div>
        </section>

        {/* Section 3: API Integration Status */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="flex items-center gap-2 border-b border-outline-variant pb-3">
            <Activity className="h-5 w-5 text-primary" />
            <h3 className="font-bold text-headline-md text-primary">
              API 端點整合狀態
            </h3>
          </div>
          <div className="space-y-2">
            {[
              { method: "POST", path: "/api/v1/analyse", desc: "啟動新分析回合" },
              { method: "GET", path: "/api/v1/stream_trace", desc: "SSE 即時推論軌跡" },
              { method: "GET", path: "/api/v1/runs", desc: "列出歷史分析" },
              { method: "GET", path: "/api/v1/runs/:id", desc: "取得單一分析結果" },
              { method: "GET", path: "/api/v1/health", desc: "系統健康檢查" },
            ].map((endpoint) => (
              <div
                key={endpoint.path}
                className="flex items-center gap-3 rounded-lg border border-outline-variant bg-surface-container-low px-4 py-2.5"
              >
                <span className="rounded bg-primary/10 px-2 py-0.5 font-mono text-[10px] font-bold text-primary">
                  {endpoint.method}
                </span>
                <span className="font-mono text-[12px] text-primary font-medium flex-1">
                  {endpoint.path}
                </span>
                <span className="text-[11px] text-secondary">{endpoint.desc}</span>
              </div>
            ))}
          </div>
        </section>

        {/* Section 4: Financial Trust Isolation */}
        <section className="ai-card rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 space-y-4">
          <div className="flex items-center gap-2 border-b border-outline-variant pb-3">
            <Lock className="h-5 w-5 text-primary" />
            <h3 className="font-bold text-headline-md text-primary">
              HOYA BIT 100% 法幣隔離信託與 DCA 指引
            </h3>
          </div>
          <p className="text-body-md text-secondary leading-relaxed">
            系統報告自動嵌入安納語氣 (Calibrated Tone Adjustment)。極端恐慌行情下，必定輸出信託隔離與定期定額指引。
          </p>
        </section>
      </div>
    </div>
  );
}
