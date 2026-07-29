export type Asset = "BTC" | "ETH" | "SOL" | "BNB" | "XRP" | "Market";
export type CoinAsset = "BTC" | "ETH" | "SOL" | "BNB" | "XRP";
export type Facet = "technical" | "positioning" | "fundamental" | "sentiment";
export type Stance = "bullish" | "bearish" | "neutral";
export type ExecutionStatus =
  | "planned"
  | "succeeded"
  | "unavailable"
  | "timed_out"
  | "failed";

export interface ToolExecutionRecord {
  tool: string;
  asset: Asset;
  arguments: Record<string, unknown>;
  status: ExecutionStatus;
  observation: string;
  evidence_ids: string[];
  duration_seconds: number;
}

export interface EvidenceGapState {
  missing_facets: Facet[];
  direction_balance: boolean;
  contradiction_facets: Facet[];
  independent_sources: number;
  fresh: boolean;
  reasons: string[];
}

export interface TraceStreamEvent {
  run_id: string;
  seq: number;
  kind: string;
  reason: string;
  elapsed_seconds: number;
  executions: ToolExecutionRecord[];
  evidence_ids: string[];
  gap: EvidenceGapState;
}

export interface EvidenceSource {
  source: string;
  source_url: string;
  fetched_at: string;
  content_reference: { locator: string; excerpt: string };
}

/** 證據的視覺形式。generated = 本系統自原始數值繪製；external = 引用外部既有圖表。 */
export interface EvidenceFigure {
  kind: "generated" | "external";
  caption: string;
  /** 可直接放進 img src：自繪圖為 data URI，外部圖為原始 URL。 */
  src: string;
  source_url: string | null;
  alt: string;
}

export interface EvidenceRecord {
  evidence_id: string;
  facet: Facet;
  summary: string;
  stance_hint: number;
  related_claim: string[];
  sources: EvidenceSource[];
  figures?: EvidenceFigure[];
}

export interface ReportClaim {
  text: string;
  evidence_ids: string[];
  facet: Facet;
  role: "fact" | "inference" | "conclusion" | "counter_evidence" | "risk" | "invalidation" | "watch";
}

export interface AnalysisReport {
  run_id: string;
  asset: Asset;
  question: string;
  stance: Stance;
  confidence: number | null;
  confidence_cause?: string | null;
  cutoff: string;
  analysis_window_start?: string | null;
  analysis_window_end?: string | null;
  facet_stances: Partial<Record<Facet, Stance>>;
  claims: ReportClaim[];
  evidence: EvidenceRecord[];
  enhanced_report_md?: string;
}
