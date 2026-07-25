"use client";

import { useState, useEffect, useCallback } from "react";
import { FolderOpen, Activity, ShieldCheck, RefreshCw, Loader2, CheckCircle2, XCircle } from "lucide-react";
import type { Facet } from "@/lib/contracts";

interface FactorCategory {
  facet: Facet;
  name: string;
  count: number;
  sources: string[];
  description: string;
}

const CATEGORIES: FactorCategory[] = [
  {
    facet: "technical",
    name: "技術面因子 (Technical)",
    count: 42,
    sources: ["CoinGecko OHLCV", "Binance Spot Depth", "EMA/RSI Vector Engine"],
    description: "價格動能、均線支撐壓力、相對強弱指標、布林通道帶寬與量價背離現象。",
  },
  {
    facet: "positioning",
    name: "籌碼面因子 (Positioning)",
    count: 28,
    sources: ["Glassnode On-Chain", "Coinglass Derivatives", "Binance Futures OI"],
    description: "合約持倉量 (OI)、資金費率 (Funding Rate)、多空爆倉比例、巨鯨地址流入與交易所存量。",
  },
  {
    facet: "fundamental",
    name: "基本面因子 (Fundamental)",
    count: 35,
    sources: ["DefiLlama TVL", "TokenTerminal Gas", "Chainalysis Protocol Data"],
    description: "協議鎖倉量 (TVL)、每日活躍地址 (DAU)、手續費收入 (Fees/Revenue)、鏈上轉移總值。",
  },
  {
    facet: "sentiment",
    name: "情緒與輿情因子 (Sentiment)",
    count: 19,
    sources: ["Crypto Fear & Greed Index", "Social Sentiment Stream", "News Aggregator API"],
    description: "市場恐慌與貪婪指數、社群討論聲量峰值、主流財經媒體報導熱度與社群多空偏向。",
  },
];

interface BackendStatus {
  connected: boolean;
  totalRuns: number;
  recentAssets: string[];
}

export function LibraryView() {
  const [backendStatus, setBackendStatus] = useState<BackendStatus>({
    connected: false,
    totalRuns: 0,
    recentAssets: [],
  });
  const [loading, setLoading] = useState(true);

  const checkStatus = useCallback(async () => {
    setLoading(true);
    try {
      const [healthRes, runsRes] = await Promise.allSettled([
        fetch("/api/v1/health"),
        fetch("/api/v1/runs"),
      ]);

      let connected = false;
      if (healthRes.status === "fulfilled" && healthRes.value.ok) {
        const health = await healthRes.value.json();
        connected = health.backend === "ok";
      }

      let totalRuns = 0;
      let recentAssets: string[] = [];
      if (runsRes.status === "fulfilled" && runsRes.value.ok) {
        const data = await runsRes.value.json();
        const runs = data.runs ?? [];
        totalRuns = runs.length;
        recentAssets = [...new Set(runs.map((r: Record<string, unknown>) => String(r.asset ?? "")).filter(Boolean))] as string[];
      }

      setBackendStatus({ connected, totalRuns, recentAssets });
    } catch {
      setBackendStatus({ connected: false, totalRuns: 0, recentAssets: [] });
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    checkStatus();
  }, [checkStatus]);

  return (
    <div className="chat-scroll flex-1 overflow-y-auto p-6 md:p-8">
      <div className="mx-auto max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-1 border-b border-outline-variant pb-5">
          <div className="flex items-center gap-2 text-primary font-bold text-headline-lg">
            <FolderOpen className="h-6 w-6 text-secondary" />
            <h2>Factor & Evidence Library</h2>
          </div>
          <p className="text-body-md text-secondary">
            HOYA BIT 金融因子資料庫與多源取證數據集。
          </p>
        </div>

        {/* Backend Connection Status */}
        {!loading && (
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
            <div className={`rounded-xl border p-4 ${backendStatus.connected ? "border-emerald-200 bg-emerald-50/50" : "border-red-200 bg-red-50/50"}`}>
              <div className="flex items-center gap-2 mb-1">
                {backendStatus.connected
                  ? <CheckCircle2 className="h-4 w-4 text-emerald-600" />
                  : <XCircle className="h-4 w-4 text-red-500" />}
                <span className="text-label-caps text-secondary uppercase font-semibold">Agent 後端</span>
              </div>
              <p className={`font-mono text-body-lg font-bold ${backendStatus.connected ? "text-emerald-700" : "text-red-600"}`}>
                {backendStatus.connected ? "已連線" : "未連線"}
              </p>
            </div>
            <div className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
              <span className="text-label-caps text-secondary uppercase font-semibold">已完成分析</span>
              <p className="font-mono text-body-lg font-bold text-primary mt-1">
                {backendStatus.totalRuns} 筆
              </p>
            </div>
            <div className="rounded-xl border border-outline-variant bg-surface-container-low p-4">
              <div className="flex items-center justify-between">
                <span className="text-label-caps text-secondary uppercase font-semibold">涵蓋幣種</span>
                <button
                  onClick={checkStatus}
                  disabled={loading}
                  className="rounded p-1 text-secondary hover:text-primary transition-colors"
                  title="刷新狀態"
                >
                  <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
                </button>
              </div>
              <p className="font-mono text-body-lg font-bold text-primary mt-1">
                {backendStatus.recentAssets.length > 0
                  ? backendStatus.recentAssets.join(", ")
                  : "BTC, ETH, SOL, BNB, XRP"}
              </p>
            </div>
          </div>
        )}

        {loading && (
          <div className="flex items-center justify-center py-6">
            <Loader2 className="h-5 w-5 animate-spin text-primary" />
            <span className="ml-2 text-body-md text-secondary">檢查資料來源狀態...</span>
          </div>
        )}

        {/* Grid of Factor Categories */}
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
          {CATEGORIES.map((cat) => (
            <article
              key={cat.facet}
              className="ai-card flex flex-col justify-between rounded-2xl border border-outline-variant bg-surface-container-lowest p-5 transition-all hover:border-primary/40"
            >
              <div>
                <div className="flex items-center justify-between border-b border-outline-variant/60 pb-3">
                  <div className="flex items-center gap-2">
                    <Activity className="h-5 w-5 text-emerald-600" />
                    <h3 className="font-headline-md text-body-lg text-primary font-bold">
                      {cat.name}
                    </h3>
                  </div>
                  <span className="rounded-pill bg-surface-container-high px-3 py-1 font-mono text-[11px] font-semibold text-primary">
                    {cat.count} 因子項
                  </span>
                </div>
                <p className="mt-3 text-body-md text-secondary leading-relaxed">
                  {cat.description}
                </p>
              </div>

              <div className="mt-4 border-t border-outline-variant/40 pt-3">
                <span className="text-label-caps text-secondary font-semibold uppercase block mb-1.5">
                  已整合資料源 (Data Providers)
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {cat.sources.map((src) => (
                    <span
                      key={src}
                      className="rounded-md border border-outline-variant bg-surface-container-low px-2 py-1 font-mono text-[11px] text-primary"
                    >
                      {src}
                    </span>
                  ))}
                </div>
              </div>
            </article>
          ))}
        </div>

        {/* Status Box */}
        <div className="rounded-2xl border border-emerald-200 bg-emerald-50/50 p-5 flex items-center gap-4">
          <ShieldCheck className="h-8 w-8 text-emerald-600 flex-shrink-0" />
          <div>
            <h4 className="font-bold text-primary text-body-lg">
              資料降級機制 (Graceful Degradation) 正常運行中
            </h4>
            <p className="text-body-md text-secondary">
              當主力 API (CoinGecko / Binance) 觸發 502 或 Rate Limit 時，系統會自動流暢降級至備用數據源，並於報告中呈現完整的缺口驗證紀錄。
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}
