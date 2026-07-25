"use client";

import { useState, useEffect, useCallback } from "react";
import {
  History as HistoryIcon,
  Search,
  Calendar,
  ChevronRight,
  RefreshCw,
  CheckCircle2,
  X,
  Loader2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import type { Asset, Stance, AnalysisReport, TraceStreamEvent } from "@/lib/contracts";

export interface HistoryItem {
  id: string;
  asset: Asset;
  question: string;
  stance: Stance;
  confidence: number;
  date: string;
  claimsCount: number;
  evidenceCount: number;
  report: AnalysisReport;
  events: TraceStreamEvent[];
}

/* ── Mock fallback data (used when backend is unavailable) ── */
const MOCK_HISTORY: HistoryItem[] = [
  {
    id: "run-20260725-001",
    asset: "BTC",
    question: "市場上認為 BTC 短期盤整，請驗證正反證據與多空主力佈局。",
    stance: "neutral",
    confidence: 0.82,
    date: "2026-07-25 16:30 UTC",
    claimsCount: 4,
    evidenceCount: 12,
    report: {
      run_id: "run-20260725-001",
      asset: "BTC",
      question: "市場上認為 BTC 短期盤整，請驗證正反證據與多空主力佈局。",
      stance: "neutral",
      confidence: 0.82,
      cutoff: "2026-07-25 UTC",
      facet_stances: { technical: "neutral", positioning: "bullish", fundamental: "bullish", sentiment: "neutral" },
      claims: [
        {
          text: "BTC 價格於 66,500 - 68,000 美元平穩震盪，多空資金比保持中性偏多。",
          evidence_ids: ["EV-BTC-TECH-01", "EV-BTC-POS-01"],
          facet: "technical",
          role: "fact",
        },
        {
          text: "主力合約持倉量 (OI) 增加 2.4%，但資金費率保持在 0.008% 低位，顯示無過度開槓桿現象。",
          evidence_ids: ["EV-BTC-POS-02"],
          facet: "positioning",
          role: "inference",
        },
        {
          text: "鏈上巨鯨地址近 72 小時淨流入 4,200 BTC，長線籌碼持續沉澱於冷錢包。",
          evidence_ids: ["EV-BTC-FUND-01"],
          facet: "fundamental",
          role: "conclusion",
        },
        {
          text: "風控提示：若跌破 65,200 美元強支撐位，可能引發短期止損賣壓，建議保持 DCA 策略。",
          evidence_ids: ["EV-BTC-RISK-01"],
          facet: "sentiment",
          role: "risk",
        },
      ],
      evidence: [
        {
          evidence_id: "EV-BTC-TECH-01",
          facet: "technical",
          summary: "CoinGecko BTC/USDT 4H K線均線呈收斂，RSI 位於 52.4，方向中立。",
          stance_hint: 0.05,
          related_claim: ["claim-1"],
          sources: [
            {
              source: "CoinGecko API",
              source_url: "https://api.coingecko.com/v3/coins/bitcoin",
              fetched_at: "2026-07-25 16:29 UTC",
              content_reference: { locator: "ohlc/4h", excerpt: "BTC/USDT 4H Range 66500-68000, RSI 52.4" },
            },
          ],
        },
        {
          evidence_id: "EV-BTC-POS-01",
          facet: "positioning",
          summary: "Binance/OKX 期貨未平倉合約 (OI) 增至 185 億美元，資金費率中立。",
          stance_hint: 0.2,
          related_claim: ["claim-2"],
          sources: [
            {
              source: "Binance Futures API",
              source_url: "https://fapi.binance.com/fapi/v1/premiumIndex",
              fetched_at: "2026-07-25 16:29 UTC",
              content_reference: { locator: "premiumIndex?symbol=BTCUSDT", excerpt: "Funding Rate 0.008%, OI +2.4%" },
            },
          ],
        },
      ],
    },
    events: [
      {
        run_id: "run-20260725-001",
        seq: 1,
        kind: "plan",
        reason: "發起 ReAct 推理：同時對技術面與籌碼面進行多源取證。",
        elapsed_seconds: 0.42,
        executions: [],
        evidence_ids: [],
        gap: { missing_facets: [], direction_balance: true, contradiction_facets: [], independent_sources: 2, fresh: true, reasons: [] },
      },
      {
        run_id: "run-20260725-001",
        seq: 2,
        kind: "gather",
        reason: "成功取得 CoinGecko 與 Binance 2 項核心證據",
        elapsed_seconds: 1.25,
        executions: [
          {
            tool: "coingecko_ohlcv",
            asset: "BTC",
            arguments: { asset: "BTC", interval: "4h" },
            status: "succeeded",
            observation: "1 項證據",
            evidence_ids: ["EV-BTC-TECH-01"],
            duration_seconds: 0.45,
          },
        ],
        evidence_ids: ["EV-BTC-TECH-01", "EV-BTC-POS-01"],
        gap: { missing_facets: [], direction_balance: true, contradiction_facets: [], independent_sources: 2, fresh: true, reasons: [] },
      },
    ],
  },
  {
    id: "run-20260725-002",
    asset: "ETH",
    question: "ETH/BTC 匯率對是否有反轉跡象？鏈上質押數據與 Gas 費動態說明。",
    stance: "bullish",
    confidence: 0.88,
    date: "2026-07-25 14:15 UTC",
    claimsCount: 5,
    evidenceCount: 15,
    report: {
      run_id: "run-20260725-002",
      asset: "ETH",
      question: "ETH/BTC 匯率對是否有反轉跡象？鏈上質押數據與 Gas 費動態說明。",
      stance: "bullish",
      confidence: 0.88,
      cutoff: "2026-07-25 UTC",
      facet_stances: { technical: "bullish", positioning: "bullish", fundamental: "bullish", sentiment: "neutral" },
      claims: [
        {
          text: "ETH/BTC 匯率比於 0.0505 築底反彈，鏈上 Gas 費活躍度增長 18%。",
          evidence_ids: ["EV-ETH-TECH-01"],
          facet: "technical",
          role: "fact",
        },
        {
          text: "信標鏈質押總量突破 3,450 萬 ETH，市場流通供應持續通縮。",
          evidence_ids: ["EV-ETH-STAKE-01"],
          facet: "fundamental",
          role: "conclusion",
        },
      ],
      evidence: [],
    },
    events: [],
  },
  {
    id: "run-20260724-003",
    asset: "SOL",
    question: "SOL 生態系近期 DEX 交易量與衍生品未平倉量 (OI) 異常爆發的原因。",
    stance: "bullish",
    confidence: 0.91,
    date: "2026-07-24 19:40 UTC",
    claimsCount: 6,
    evidenceCount: 18,
    report: {
      run_id: "run-20260724-003",
      asset: "SOL",
      question: "SOL 生態系近期 DEX 交易量與衍生品未平倉量 (OI) 異常爆發的原因。",
      stance: "bullish",
      confidence: 0.91,
      cutoff: "2026-07-24 UTC",
      facet_stances: { technical: "bullish", positioning: "bullish", fundamental: "bullish", sentiment: "bullish" },
      claims: [
        {
          text: "SOL DEX 24小時交易量超越 18 億美元，Raydium 與 Jupiter 貢獻過半數據。",
          evidence_ids: ["EV-SOL-DEX-01"],
          facet: "fundamental",
          role: "fact",
        },
      ],
      evidence: [],
    },
    events: [],
  },
];

/* ── Transform backend run payload to HistoryItem ── */
function backendRunToHistoryItem(run: Record<string, unknown>): HistoryItem | null {
  try {
    const runId = String(run.run_id ?? "");
    const asset = String(run.asset ?? "BTC") as Asset;
    const question = String(run.question ?? "");
    const stance = String(run.stance ?? "neutral") as Stance;
    const confidence = typeof run.confidence === "number" ? run.confidence : 0;
    const claims = Array.isArray(run.claims) ? run.claims : [];
    const evidence = Array.isArray(run.evidence) ? run.evidence : [];
    const cutoff = String(run.cutoff ?? "");
    const facetStances = (run.facet_stances ?? {}) as Record<string, string>;

    if (!runId || !question) return null;

    const report: AnalysisReport = {
      run_id: runId,
      asset,
      question,
      stance,
      confidence,
      cutoff,
      facet_stances: facetStances as AnalysisReport["facet_stances"],
      claims: claims.map((c: Record<string, unknown>) => ({
        text: String(c.text ?? ""),
        evidence_ids: Array.isArray(c.evidence_ids) ? c.evidence_ids.map(String) : [],
        facet: String(c.facet ?? "technical") as AnalysisReport["claims"][number]["facet"],
        role: String(c.role ?? "fact") as AnalysisReport["claims"][number]["role"],
      })),
      evidence: evidence.map((e: Record<string, unknown>) => ({
        evidence_id: String(e.evidence_id ?? ""),
        facet: String(e.facet ?? "technical") as AnalysisReport["evidence"][number]["facet"],
        summary: String(e.summary ?? ""),
        stance_hint: typeof e.stance_hint === "number" ? e.stance_hint : 0,
        related_claim: Array.isArray(e.related_claim) ? e.related_claim.map(String) : [],
        sources: Array.isArray(e.sources)
          ? e.sources.map((s: Record<string, unknown>) => ({
              source: String(s.source ?? ""),
              source_url: String(s.source_url ?? ""),
              fetched_at: String(s.fetched_at ?? ""),
              content_reference: {
                locator: String((s.content_reference as Record<string, unknown>)?.locator ?? ""),
                excerpt: String((s.content_reference as Record<string, unknown>)?.excerpt ?? ""),
              },
            }))
          : [],
      })),
    };

    return {
      id: runId,
      asset,
      question,
      stance,
      confidence,
      date: cutoff || new Date().toISOString(),
      claimsCount: claims.length,
      evidenceCount: evidence.length,
      report,
      events: [],
    };
  } catch {
    return null;
  }
}

interface HistoryViewProps {
  onLoadRun?: (item: HistoryItem) => void;
}

export function HistoryView({ onLoadRun }: HistoryViewProps) {
  const [searchTerm, setSearchTerm] = useState("");
  const [selectedModalItem, setSelectedModalItem] = useState<HistoryItem | null>(null);
  const [history, setHistory] = useState<HistoryItem[]>(MOCK_HISTORY);
  const [loading, setLoading] = useState(true);
  const [backendConnected, setBackendConnected] = useState(false);

  const fetchHistory = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/v1/runs");
      if (res.ok) {
        const data = await res.json();
        const runs: HistoryItem[] = (data.runs ?? [])
          .map((run: Record<string, unknown>) => backendRunToHistoryItem(run))
          .filter(Boolean) as HistoryItem[];

        if (runs.length > 0) {
          setHistory(runs);
          setBackendConnected(true);
        } else {
          // No runs from backend — use mock data as demo
          setHistory(MOCK_HISTORY);
          setBackendConnected(false);
        }
      } else {
        setHistory(MOCK_HISTORY);
        setBackendConnected(false);
      }
    } catch {
      setHistory(MOCK_HISTORY);
      setBackendConnected(false);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchHistory();
  }, [fetchHistory]);

  const filteredHistory = history.filter(
    (item) =>
      item.question.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.asset.toLowerCase().includes(searchTerm.toLowerCase()) ||
      item.id.toLowerCase().includes(searchTerm.toLowerCase()),
  );

  return (
    <div className="chat-scroll flex-1 overflow-y-auto p-6 md:p-8">
      <div className="mx-auto max-w-4xl space-y-6">
        {/* Header */}
        <div className="flex flex-col gap-1 border-b border-outline-variant pb-5">
          <div className="flex items-center gap-2 text-primary font-bold text-headline-lg">
            <HistoryIcon className="h-6 w-6 text-secondary" />
            <h2>Analysis History</h2>
          </div>
          <p className="text-body-md text-secondary">
            過去執行的 ReAct Agent 分析記錄與可稽核報告歷史。點擊「載入詳細報告」可將完整推論與證據載入 Workspace。
          </p>
        </div>

        {/* Connection status banner */}
        {!loading && (
          <div
            className={`flex items-center gap-3 rounded-xl border px-4 py-2.5 text-[12px] ${
              backendConnected
                ? "border-emerald-200 bg-emerald-50/50 text-emerald-800"
                : "border-amber-200 bg-amber-50/50 text-amber-800"
            }`}
          >
            <CheckCircle2 className="h-4 w-4 flex-shrink-0" />
            <span>
              {backendConnected
                ? `已連接後端 Agent — 顯示 ${history.length} 筆真實分析記錄`
                : "後端未連接，目前顯示示範資料 (Demo Mode)。啟動 Python 後端以載入真實歷史。"}
            </span>
          </div>
        )}

        {/* Filter bar */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-secondary" />
            <input
              type="text"
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              placeholder="搜尋歷史紀錄或問題關鍵字..."
              className="w-full rounded-xl border border-outline-variant bg-surface-container-lowest py-2 pl-9 pr-4 text-body-md text-primary placeholder:text-secondary focus:border-primary focus:outline-none"
            />
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={fetchHistory}
              disabled={loading}
              className="flex items-center gap-1.5 rounded-lg border border-outline-variant px-3 py-1.5 text-xs text-secondary hover:bg-surface-container-low hover:text-primary transition-colors disabled:opacity-50"
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? "animate-spin" : ""}`} />
              重新載入
            </button>
            <span className="text-mono-label text-secondary font-mono">
              Total {filteredHistory.length} Runs
            </span>
          </div>
        </div>

        {/* Loading state */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-6 w-6 animate-spin text-primary" />
            <span className="ml-3 text-body-md text-secondary">載入分析歷史中...</span>
          </div>
        )}

        {/* History items list */}
        {!loading && (
          <div className="space-y-3">
            {filteredHistory.map((item) => (
              <article
                key={item.id}
                className="ai-card flex flex-col gap-3 rounded-2xl border border-outline-variant bg-surface-container-lowest p-5 transition-all hover:border-primary/40 hover:shadow-card-hover"
              >
                <div className="flex flex-wrap items-center justify-between gap-2 border-b border-outline-variant/60 pb-3">
                  <div className="flex items-center gap-2.5">
                    <span className="rounded-pill bg-primary px-3 py-1 font-mono text-[12px] font-bold text-on-primary">
                      {item.asset}
                    </span>
                    <span className="font-mono text-mono-label text-secondary">
                      {item.id.length > 20 ? `${item.id.slice(0, 8)}...${item.id.slice(-4)}` : item.id}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span
                      className={`rounded-pill px-3 py-0.5 text-label-caps font-bold ${
                        item.stance === "bullish"
                          ? "bg-emerald-100 text-emerald-700"
                          : item.stance === "bearish"
                          ? "bg-red-100 text-red-700"
                          : "bg-amber-100 text-amber-800"
                      }`}
                    >
                      {item.stance.toUpperCase()}
                    </span>
                    <span className="font-mono text-mono-label text-secondary">
                      置信度: <strong>{Math.round(item.confidence * 100)}%</strong>
                    </span>
                  </div>
                </div>

                <p className="text-body-md text-primary font-medium">
                  {item.question}
                </p>

                <div className="flex flex-wrap items-center justify-between text-mono-label text-secondary pt-2">
                  <div className="flex items-center gap-4">
                    <span>
                      判斷條目: <strong className="text-primary">{item.claimsCount}</strong>
                    </span>
                    <span>
                      交叉證據: <strong className="text-primary">{item.evidenceCount}</strong>
                    </span>
                    <span className="flex items-center gap-1">
                      <Calendar className="h-3.5 w-3.5" />
                      {item.date}
                    </span>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => setSelectedModalItem(item)}
                      className="rounded-lg border border-outline-variant px-3 py-1 text-xs text-secondary hover:bg-surface-container-low hover:text-primary transition-colors"
                    >
                      快速預覽
                    </button>
                    <Button
                      onClick={() => onLoadRun?.(item)}
                      className="gap-1 text-xs py-1 px-3"
                    >
                      載入詳細報告 <ChevronRight className="h-3.5 w-3.5" />
                    </Button>
                  </div>
                </div>
              </article>
            ))}

            {filteredHistory.length === 0 && !loading && (
              <div className="flex flex-col items-center gap-3 py-12 text-center">
                <HistoryIcon className="h-10 w-10 text-outline" />
                <p className="text-body-md text-secondary">
                  {searchTerm ? "找不到符合搜尋條件的歷史記錄" : "尚無分析記錄"}
                </p>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Modal Preview Drawer */}
      {selectedModalItem && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 backdrop-blur-sm p-4">
          <div className="w-full max-w-2xl rounded-2xl border border-outline-variant bg-surface-container-lowest p-6 shadow-dropdown space-y-4 max-h-[85vh] overflow-y-auto chat-scroll animate-fade-in">
            <div className="flex items-center justify-between border-b border-outline-variant pb-3">
              <div className="flex items-center gap-2">
                <span className="rounded-pill bg-primary px-3 py-1 font-mono text-[12px] font-bold text-on-primary">
                  {selectedModalItem.asset}
                </span>
                <h3 className="font-bold text-headline-md text-primary">
                  報告預覽
                </h3>
              </div>
              <button
                onClick={() => setSelectedModalItem(null)}
                className="rounded-md p-1.5 text-secondary hover:bg-surface-container-low"
              >
                <X className="h-5 w-5" />
              </button>
            </div>

            <div className="space-y-3">
              <p className="font-bold text-primary text-body-lg">
                {selectedModalItem.question}
              </p>
              <div className="flex items-center gap-4 text-xs font-mono text-secondary border-y border-outline-variant/60 py-2">
                <span>方向: <strong>{selectedModalItem.stance.toUpperCase()}</strong></span>
                <span>置信度: <strong>{Math.round(selectedModalItem.confidence * 100)}%</strong></span>
                <span>日期: {selectedModalItem.date}</span>
              </div>

              {selectedModalItem.report.claims.length > 0 ? (
                <div className="space-y-2 pt-2">
                  <h4 className="font-semibold text-xs text-secondary uppercase tracking-wider">
                    核心判斷條目 (Claims)
                  </h4>
                  {selectedModalItem.report.claims.map((claim, idx) => (
                    <div key={idx} className="rounded-xl border border-outline-variant bg-surface-container-low p-3 text-xs leading-relaxed text-primary">
                      <span className="font-bold text-[10px] uppercase text-emerald-700 block mb-1">
                        [{claim.role}] {claim.facet}
                      </span>
                      {claim.text}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-xs text-secondary italic">暫無結構化判斷預覽</p>
              )}
            </div>

            <div className="flex justify-end gap-2 pt-3 border-t border-outline-variant">
              <Button
                variant="secondary"
                onClick={() => setSelectedModalItem(null)}
                className="text-xs"
              >
                關閉
              </Button>
              <Button
                onClick={() => {
                  const item = selectedModalItem;
                  setSelectedModalItem(null);
                  onLoadRun?.(item);
                }}
                className="text-xs gap-1"
              >
                在 Workspace 開啟全功能稽核 <ChevronRight className="h-3.5 w-3.5" />
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
