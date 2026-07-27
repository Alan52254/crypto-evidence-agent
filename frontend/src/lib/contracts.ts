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

export interface EvidenceRecord {
  evidence_id: string;
  facet: Facet;
  summary: string;
  stance_hint: number;
  related_claim: string[];
  sources: EvidenceSource[];
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
  facet_stances: Partial<Record<Facet, Stance>>;
  claims: ReportClaim[];
  evidence: EvidenceRecord[];
  enhanced_report_md?: string;
}
