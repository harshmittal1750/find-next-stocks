import type { DashboardPayload, Stock } from "@/lib/types";

const EMPTY_DASHBOARD: DashboardPayload = {
  schema_version: 3,
  generated_at: null,
  record_count: 0,
  stocks: [],
  data_status: "offline",
};

function cleanStock(stock: Stock): Stock {
  const issues = [...(stock.data_quality?.issues ?? [])];
  const cleaned = { ...stock };
  for (const field of ["promoter_pct", "institutional_pct"] as const) {
    const value = cleaned[field];
    if (value !== null && value !== undefined && (value < 0 || value > 100)) {
      issues.push({
        code: "percent_out_of_range",
        field,
        message: `${field} must be between 0% and 100%`,
        raw_value: value,
      });
      cleaned[field] = null;
    }
  }
  if (
    cleaned.promoter_pct !== null &&
    cleaned.institutional_pct !== null &&
    cleaned.promoter_pct + cleaned.institutional_pct > 100.5
  ) {
    issues.push({
      code: "ownership_total_exceeds_100",
      field: "institutional_pct",
      message: "Promoter and institutional ownership overlap beyond 100%",
      raw_value: cleaned.institutional_pct,
    });
    cleaned.institutional_pct = null;
  }
  cleaned.data_quality = { status: issues.length ? "review" : "valid", issues };
  return cleaned;
}

function normalize(payload: DashboardPayload): DashboardPayload {
  const stocks = (payload.stocks ?? []).map(cleanStock);
  return {
    ...payload,
    stocks,
    record_count: stocks.length,
  };
}

export async function getDashboard(): Promise<DashboardPayload> {
  const apiBase = process.env.API_BASE_URL ?? "http://127.0.0.1:8000";
  try {
    const response = await fetch(`${apiBase}/api/v1/dashboard`, {
      cache: "no-store",
      signal: AbortSignal.timeout(8000),
    });
    if (!response.ok) {
      throw new Error(`Dashboard API returned ${response.status}`);
    }
    return normalize((await response.json()) as DashboardPayload);
  } catch {
    return EMPTY_DASHBOARD;
  }
}
