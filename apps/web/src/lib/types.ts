export type DataQuality = {
  status: "valid" | "review";
  issues: Array<{
    code: string;
    field: string;
    message: string;
    raw_value: unknown;
  }>;
};

export type Stock = {
  rank: number | null;
  ticker: string;
  shortName: string;
  sector: string | null;
  industry?: string | null;
  mcap_cr: number | null;
  currentPrice: number | null;
  trailingPE: number | null;
  forwardPE?: number | null;
  priceToBook?: number | null;
  pegRatio?: number | null;
  roe_pct: number | null;
  returnOnAssets?: number | null;
  debtToEquity?: number | null;
  currentRatio?: number | null;
  quickRatio?: number | null;
  earningsGrowth?: number | null;
  earningsQuarterlyGrowth?: number | null;
  revenueGrowth?: number | null;
  profitMargins?: number | null;
  operatingMargins?: number | null;
  grossMargins?: number | null;
  ebitdaMargins?: number | null;
  freeCashflow?: number | null;
  promoter_pct: number | null;
  institutional_pct: number | null;
  institutions_count?: number | null;
  avg_delivery_pct?: number | null;
  pct_below_52w_high: number | null;
  pct_above_52w_low?: number | null;
  upside_pct?: number | null;
  targetMeanPrice?: number | null;
  numberOfAnalystOpinions?: number | null;
  recommendationKey?: string | null;
  fiftyTwoWeekHigh?: number | null;
  fiftyTwoWeekLow?: number | null;
  fiftyDayAverage?: number | null;
  twoHundredDayAverage?: number | null;
  beta?: number | null;
  rsi14?: number | null;
  rsiSignal?: "oversold" | "neutral" | "overbought" | null;
  data_cov: number | null;
  quality_cov?: number | null;
  valuation_cov?: number | null;
  g_quality?: number | null;
  g_smart_money?: number | null;
  g_valuation?: number | null;
  g_growth?: number | null;
  g_price_setup?: number | null;
  g_analyst?: number | null;
  g_momentum?: number | null;
  model_score?: number | null;
  final_score: number | null;
  rank_vs_staged?: number | null;
  score_vs_staged?: number | null;
  rank_chg?: number | null;
  price_chg_pct?: number | null;
  // Which source resolved each field: "observation" (live fetch), "ranking" (this
  // run's scoring output) or "archive" (the imported CSV snapshot). Sent for every
  // stock; the explorer uses it to mark archived values as not freshly fetched.
  field_origins?: Record<string, string>;
  data_quality?: DataQuality;
};

export type DashboardSource = {
  name: string;
  source_path: string;
  source_modified_at: string;
  database_imported_at: string;
  row_count: number;
  sha256: string;
};

export type RefreshStage = {
  stage_id: string;
  provider: string | null;
  label: string;
  status: "pending" | "running" | "completed" | "skipped" | "failed";
  progress: number;
  processed: number;
  total: number;
  observations_written: number;
  message: string;
  started_at: string | null;
  completed_at: string | null;
};

export type RefreshJob = {
  job_id: string;
  status: "queued" | "running" | "completed" | "completed_with_warnings" | "failed";
  progress: number;
  created_at: string;
  started_at: string | null;
  completed_at: string | null;
  total_stocks: number;
  selected_providers: string[];
  observations_written: number;
  raw_responses_archived: number;
  message: string;
  stages: RefreshStage[];
};

export type RefreshProvider = {
  provider: string;
  label: string;
  available: boolean;
  reason: string | null;
  last_refresh_at: string | null;
};

export type DashboardPayload = {
  schema_version: number;
  generated_at: string | null;
  refreshed?: string;
  record_count: number;
  field_count?: number;
  stocks: Stock[];
  sources?: DashboardSource[];
  freshness?: {
    latest_source_at: string;
    checked_at: string;
    scope: string;
  };
  data_status: "ready" | "fallback" | "empty" | "offline";
  served_from?: string;
};
