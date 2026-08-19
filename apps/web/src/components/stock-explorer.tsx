"use client";

import { Fragment, useMemo, useState } from "react";

import { DataRefreshControl } from "@/components/data-refresh-control";
import type { DashboardPayload, Stock } from "@/lib/types";

type SortKey =
  | "rank"
  | "shortName"
  | "currentPrice"
  | "final_score"
  | "data_cov"
  | "trailingPE"
  | "priceToBook"
  | "roe_pct"
  | "debtToEquity"
  | "promoter_pct"
  | "institutional_pct"
  | "pct_below_52w_high"
  | "upside_pct"
  | "mcap_cr"
  | "rank_vs_staged";

type SortDirection = "asc" | "desc";

const NUMBER = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 1 });
const INTEGER = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
const CURRENCY = new Intl.NumberFormat("en-IN", {
  style: "currency",
  currency: "INR",
  maximumFractionDigits: 1,
});

const COLUMNS: Array<{ key: SortKey; label: string; align?: "right" }> = [
  { key: "rank", label: "Rank" },
  { key: "shortName", label: "Company" },
  { key: "currentPrice", label: "Price", align: "right" },
  { key: "final_score", label: "Score", align: "right" },
  { key: "data_cov", label: "Coverage", align: "right" },
  { key: "trailingPE", label: "P/E", align: "right" },
  { key: "priceToBook", label: "P/B", align: "right" },
  { key: "roe_pct", label: "ROE", align: "right" },
  { key: "debtToEquity", label: "Debt/Eq", align: "right" },
  { key: "promoter_pct", label: "Promoter", align: "right" },
  { key: "institutional_pct", label: "Institution", align: "right" },
  { key: "pct_below_52w_high", label: "Below high", align: "right" },
  { key: "upside_pct", label: "Upside", align: "right" },
  { key: "mcap_cr", label: "Market cap", align: "right" },
  { key: "rank_vs_staged", label: "Rank change", align: "right" },
];

const DEFAULT_DIRECTION: Record<SortKey, SortDirection> = {
  rank: "asc",
  shortName: "asc",
  currentPrice: "desc",
  final_score: "desc",
  data_cov: "desc",
  trailingPE: "asc",
  priceToBook: "asc",
  roe_pct: "desc",
  debtToEquity: "asc",
  promoter_pct: "desc",
  institutional_pct: "desc",
  pct_below_52w_high: "desc",
  upside_pct: "desc",
  mcap_cr: "desc",
  rank_vs_staged: "desc",
};

function number(value: number | null | undefined, suffix = "") {
  return value === null || value === undefined ? "—" : `${NUMBER.format(value)}${suffix}`;
}

function currency(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : CURRENCY.format(value);
}

function crores(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `₹${INTEGER.format(value)} cr`;
}

function ratioPercent(value: number | null | undefined) {
  return value === null || value === undefined ? "—" : `${NUMBER.format(value * 100)}%`;
}

function signed(value: number | null | undefined, suffix = "") {
  if (value === null || value === undefined) return "—";
  return `${value > 0 ? "+" : ""}${NUMBER.format(value)}${suffix}`;
}

function median(values: number[]) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

function scoreBands(stocks: Stock[]) {
  const bands = [
    { label: "65+", min: 65, max: Infinity },
    { label: "55–65", min: 55, max: 65 },
    { label: "45–55", min: 45, max: 55 },
    { label: "35–45", min: 35, max: 45 },
    { label: "<35", min: -Infinity, max: 35 },
  ].map((band) => ({
    ...band,
    count: stocks.filter((stock) => {
      const score = stock.final_score;
      return score !== null && score >= band.min && score < band.max;
    }).length,
  }));
  return { bands, max: Math.max(1, ...bands.map((band) => band.count)) };
}

function compareStocks(left: Stock, right: Stock, key: SortKey, direction: SortDirection) {
  const a = left[key];
  const b = right[key];
  if (a === null || a === undefined || a === "") return b === null || b === undefined || b === "" ? 0 : 1;
  if (b === null || b === undefined || b === "") return -1;
  const result =
    typeof a === "string" && typeof b === "string"
      ? a.localeCompare(b, "en", { sensitivity: "base" })
      : Number(a) - Number(b);
  return direction === "asc" ? result : -result;
}

function DetailGroup({
  title,
  metrics,
}: {
  title: string;
  metrics: Array<[string, string]>;
}) {
  return (
    <div>
      <h4 className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">{title}</h4>
      <dl className="mt-3 grid grid-cols-2 gap-x-5 gap-y-3">
        {metrics.map(([label, value]) => (
          <div className="border-t border-stone-800/80 pt-2" key={label}>
            <dt className="text-[11px] text-stone-600">{label}</dt>
            <dd className="mt-1 font-mono text-xs text-stone-300">{value}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function StockDetails({ stock }: { stock: Stock }) {
  return (
    <div className="grid gap-7 bg-stone-950/70 px-4 py-6 lg:grid-cols-4 lg:px-6">
      <DetailGroup
        metrics={[
          ["Forward P/E", number(stock.forwardPE)],
          ["PEG ratio", number(stock.pegRatio)],
          ["ROA", ratioPercent(stock.returnOnAssets)],
          ["Current ratio", number(stock.currentRatio)],
          ["Quick ratio", number(stock.quickRatio)],
          ["Free cash flow", currency(stock.freeCashflow)],
        ]}
        title="Fundamentals"
      />
      <DetailGroup
        metrics={[
          ["Revenue growth", ratioPercent(stock.revenueGrowth)],
          ["Earnings growth", ratioPercent(stock.earningsGrowth)],
          ["Quarterly earnings", ratioPercent(stock.earningsQuarterlyGrowth)],
          ["Profit margin", ratioPercent(stock.profitMargins)],
          ["Operating margin", ratioPercent(stock.operatingMargins)],
          ["EBITDA margin", ratioPercent(stock.ebitdaMargins)],
        ]}
        title="Growth & margins"
      />
      <DetailGroup
        metrics={[
          ["52-week range", `${currency(stock.fiftyTwoWeekLow)} – ${currency(stock.fiftyTwoWeekHigh)}`],
          ["50-day average", currency(stock.fiftyDayAverage)],
          ["200-day average", currency(stock.twoHundredDayAverage)],
          ["Above 52w low", number(stock.pct_above_52w_low, "%")],
          ["Delivery average", number(stock.avg_delivery_pct, "%")],
          ["Beta", number(stock.beta)],
        ]}
        title="Market setup"
      />
      <DetailGroup
        metrics={[
          ["Quality", number(stock.g_quality)],
          ["Valuation", number(stock.g_valuation)],
          ["Growth", number(stock.g_growth)],
          ["Smart money", number(stock.g_smart_money)],
          ["Price setup", number(stock.g_price_setup)],
          ["Momentum", number(stock.g_momentum)],
          ["Analyst target", currency(stock.targetMeanPrice)],
          ["Analyst opinions", number(stock.numberOfAnalystOpinions)],
          ["Score change", signed(stock.score_vs_staged)],
        ]}
        title="Model & analyst"
      />
      {stock.data_quality?.issues.length ? (
        <div className="border-l-2 border-[var(--accent)] pl-4 lg:col-span-4">
          <p className="font-mono text-[10px] uppercase tracking-[0.14em] text-[var(--accent)]">
            Validation review
          </p>
          <ul className="mt-2 space-y-1 text-xs leading-5 text-stone-500">
            {stock.data_quality.issues.map((issue, index) => (
              <li key={`${issue.code}-${issue.field}-${index}`}>{issue.message}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  );
}

export function StockExplorer({ dashboard }: { dashboard: DashboardPayload }) {
  const [query, setQuery] = useState("");
  const [sector, setSector] = useState("all");
  const [minimumCoverage, setMinimumCoverage] = useState(60);
  const [sortKey, setSortKey] = useState<SortKey>("rank");
  const [sortDirection, setSortDirection] = useState<SortDirection>("asc");
  const [pageSize, setPageSize] = useState(100);
  const [page, setPage] = useState(1);
  const [expandedTicker, setExpandedTicker] = useState<string | null>(null);

  const sectors = useMemo(
    () =>
      Array.from(new Set(dashboard.stocks.map((stock) => stock.sector).filter(Boolean))).sort() as string[],
    [dashboard.stocks],
  );

  const filtered = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase();
    const records = dashboard.stocks.filter((stock) => {
      const matchesQuery =
        !needle ||
        stock.ticker.toLocaleLowerCase().includes(needle) ||
        stock.shortName.toLocaleLowerCase().includes(needle);
      const matchesSector = sector === "all" || stock.sector === sector;
      const matchesCoverage = (stock.data_cov ?? 0) >= minimumCoverage;
      return matchesQuery && matchesSector && matchesCoverage;
    });
    return records.sort((left, right) => compareStocks(left, right, sortKey, sortDirection));
  }, [dashboard.stocks, minimumCoverage, query, sector, sortDirection, sortKey]);

  const totalPages = pageSize === 0 ? 1 : Math.max(1, Math.ceil(filtered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const visible = pageSize === 0 ? filtered : filtered.slice((safePage - 1) * pageSize, safePage * pageSize);
  const firstVisible = filtered.length ? (safePage - 1) * (pageSize || filtered.length) + 1 : 0;
  const lastVisible = filtered.length ? firstVisible + visible.length - 1 : 0;

  const coverage = median(
    dashboard.stocks.map((stock) => stock.data_cov).filter((value): value is number => value !== null),
  );
  const reviewCount = dashboard.stocks.filter((stock) => stock.data_quality?.status === "review").length;
  const distribution = scoreBands(dashboard.stocks);
  const generated = dashboard.generated_at
    ? new Intl.DateTimeFormat("en-IN", { dateStyle: "medium", timeStyle: "short" }).format(
        new Date(dashboard.generated_at),
      )
    : "No data yet";
  const isReady = dashboard.data_status === "ready";
  const statusLabel = isReady
    ? "Database ready"
    : dashboard.data_status === "fallback"
      ? "JSON fallback"
      : dashboard.data_status === "empty"
        ? "No data"
        : "API offline";

  function selectSort(nextKey: SortKey) {
    if (nextKey === sortKey) {
      setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
    } else {
      setSortKey(nextKey);
      setSortDirection(DEFAULT_DIRECTION[nextKey]);
    }
    setPage(1);
  }

  function toggleDetails(ticker: string) {
    setExpandedTicker((current) => (current === ticker ? null : ticker));
  }

  return (
    <div className="mx-auto flex w-full max-w-[1720px] flex-col px-4 pb-16 sm:px-6 lg:px-10">
      <header className="flex min-h-16 items-center justify-between border-b border-stone-800/90 py-3">
        <div className="flex items-baseline gap-3">
          <span className="text-sm font-semibold tracking-[-0.02em] text-stone-50">Find Next Stocks</span>
          <span className="hidden font-mono text-[10px] uppercase tracking-[0.18em] text-stone-500 sm:inline">
            Research terminal
          </span>
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden items-center gap-2 font-mono text-[10px] uppercase tracking-[0.14em] text-stone-500 sm:flex">
            <span
              aria-hidden="true"
              className={`h-1.5 w-1.5 rounded-full ${isReady ? "bg-[var(--accent)]" : "bg-stone-600"}`}
            />
            {statusLabel}
          </div>
          <DataRefreshControl />
        </div>
      </header>

      <section className="grid gap-8 border-b border-stone-800/90 py-10 lg:grid-cols-[minmax(0,1.5fr)_minmax(280px,0.5fr)] lg:items-end lg:py-14">
        <div>
          <p className="mb-4 font-mono text-[11px] uppercase tracking-[0.18em] text-[var(--accent)]">
            Evidence-led equity screening
          </p>
          <h1 className="max-w-4xl text-4xl font-medium tracking-[-0.045em] text-stone-50 sm:text-5xl lg:text-6xl">
            Research the ranking. Inspect the evidence.
          </h1>
          <p className="mt-5 max-w-2xl text-sm leading-6 text-stone-400 sm:text-base">
            The API merges the latest available ranking, fundamentals, shareholding, screen, and rank-history
            records stored in TimescaleDB. Every ownership value is validated before display.
          </p>
        </div>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-5 border-stone-800 lg:border-l lg:pl-8">
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">Latest source</dt>
            <dd className="mt-2 text-xs text-stone-300">{generated}</dd>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">Serving from</dt>
            <dd className="mt-2 text-xs text-stone-300">{dashboard.served_from ?? "API unavailable"}</dd>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">Merged sources</dt>
            <dd className="mt-2 text-xs text-stone-300">{dashboard.sources?.length ?? 0}</dd>
          </div>
          <div>
            <dt className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">Available fields</dt>
            <dd className="mt-2 text-xs text-stone-300">{dashboard.field_count ?? "—"}</dd>
          </div>
        </dl>
      </section>

      <section className="grid border-b border-stone-800/90 sm:grid-cols-2 lg:grid-cols-4">
        {[
          ["Tracked universe", NUMBER.format(dashboard.record_count), "stocks"],
          ["Median coverage", number(coverage, "%"), "available fields"],
          ["Sector breadth", NUMBER.format(sectors.length), "classified groups"],
          ["Needs review", NUMBER.format(reviewCount), "validation flags"],
        ].map(([label, value, note], index) => (
          <div
            className={`py-6 sm:px-6 ${index > 0 ? "border-stone-800/90 sm:border-l" : ""} ${index % 2 ? "pl-6" : ""} sm:first:pl-0 lg:first:px-0 lg:last:pr-0`}
            key={label}
          >
            <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">{label}</p>
            <p className="mt-3 text-3xl font-medium tracking-[-0.04em] text-stone-100">{value}</p>
            <p className="mt-1 text-xs text-stone-600">{note}</p>
          </div>
        ))}
      </section>

      <section className="grid gap-8 border-b border-stone-800/90 py-8 lg:grid-cols-[minmax(0,1fr)_320px]">
        <div>
          <div className="mb-5 flex items-baseline justify-between">
            <div>
              <p className="text-sm font-medium text-stone-200">Score distribution</p>
              <p className="mt-1 text-xs text-stone-600">Current final-score bands</p>
            </div>
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-stone-600">Higher is better</span>
          </div>
          <div className="grid h-36 grid-cols-5 items-end gap-3 border-b border-stone-800 pb-3">
            {distribution.bands.map((band) => (
              <div className="flex h-full flex-col justify-end gap-2" key={band.label}>
                <span className="font-mono text-[10px] text-stone-500">{band.count}</span>
                <div
                  className="min-h-1 bg-[var(--accent)] transition-[height] duration-300"
                  style={{ height: `${Math.max(4, (band.count / distribution.max) * 92)}px` }}
                />
                <span className="font-mono text-[10px] text-stone-600">{band.label}</span>
              </div>
            ))}
          </div>
        </div>
        <div className="border-t border-stone-800 pt-6 lg:border-l lg:border-t-0 lg:pl-8 lg:pt-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.16em] text-stone-600">Quality rule</p>
          <p className="mt-4 text-lg leading-7 tracking-[-0.02em] text-stone-200">
            Ownership cannot exceed 100%.
          </p>
          <p className="mt-3 text-sm leading-6 text-stone-500">
            Values outside 0–100% are retained in the archive for audit, marked invalid, and omitted from the
            canonical table.
          </p>
        </div>
      </section>

      <section className="py-8">
        <div className="mb-6 flex flex-col gap-5 lg:flex-row lg:items-end lg:justify-between">
          <div>
            <h2 className="text-xl font-medium tracking-[-0.03em] text-stone-100">Stock explorer</h2>
            <p className="mt-1 text-xs text-stone-600">
              Showing {NUMBER.format(firstVisible)}–{NUMBER.format(lastVisible)} of {NUMBER.format(filtered.length)} matches
            </p>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-[260px_200px]">
            <label className="block">
              <span className="sr-only">Search stocks</span>
              <input
                className="h-10 w-full rounded-md border border-stone-800 bg-stone-900/50 px-3 text-sm text-stone-200 outline-none transition-colors placeholder:text-stone-700 focus:border-[var(--accent)]"
                onChange={(event) => {
                  setQuery(event.target.value);
                  setPage(1);
                }}
                placeholder="Search ticker or company"
                type="search"
                value={query}
              />
            </label>
            <label className="block">
              <span className="sr-only">Sector</span>
              <select
                className="h-10 w-full rounded-md border border-stone-800 bg-stone-900/50 px-3 text-sm text-stone-300 outline-none transition-colors focus:border-[var(--accent)]"
                onChange={(event) => {
                  setSector(event.target.value);
                  setPage(1);
                }}
                value={sector}
              >
                <option value="all">All sectors</option>
                {sectors.map((item) => (
                  <option key={item} value={item}>{item}</option>
                ))}
              </select>
            </label>
          </div>
        </div>

        <div className="mb-5 flex flex-wrap items-center gap-x-4 gap-y-2 border-y border-stone-800/90 py-3">
          <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-stone-600">Minimum coverage</span>
          {[0, 40, 60, 80].map((value) => (
            <button
              className={`min-h-8 rounded-md px-2.5 font-mono text-[11px] transition-colors active:translate-y-px ${
                minimumCoverage === value
                  ? "bg-[var(--accent)] text-stone-950"
                  : "text-stone-500 hover:bg-stone-900 hover:text-stone-200"
              }`}
              key={value}
              onClick={() => {
                setMinimumCoverage(value);
                setPage(1);
              }}
              type="button"
            >
              {value}%
            </button>
          ))}
          <div className="ml-auto hidden items-center gap-2 md:flex">
            <span className="font-mono text-[10px] uppercase tracking-[0.14em] text-stone-600">Click a heading to sort</span>
          </div>
          <div className="grid w-full grid-cols-[1fr_auto] gap-2 md:hidden">
            <label>
              <span className="sr-only">Sort stocks</span>
              <select
                className="h-10 w-full rounded-md border border-stone-800 bg-stone-900/50 px-3 text-sm text-stone-300 outline-none focus:border-[var(--accent)]"
                onChange={(event) => selectSort(event.target.value as SortKey)}
                value={sortKey}
              >
                {COLUMNS.map((column) => (
                  <option key={column.key} value={column.key}>Sort: {column.label}</option>
                ))}
              </select>
            </label>
            <button
              className="h-10 rounded-md border border-stone-800 px-3 font-mono text-[11px] uppercase text-stone-400 active:translate-y-px"
              onClick={() => {
                setSortDirection((current) => (current === "asc" ? "desc" : "asc"));
                setPage(1);
              }}
              type="button"
            >
              {sortDirection}
            </button>
          </div>
        </div>

        {filtered.length ? (
          <>
            <div className="hidden overflow-x-auto border-b border-stone-800 md:block">
              <table className="w-full min-w-[1740px] border-collapse text-left">
                <thead>
                  <tr className="border-b border-stone-800 font-mono text-[10px] uppercase tracking-[0.12em] text-stone-600">
                    {COLUMNS.map((column) => {
                      const active = sortKey === column.key;
                      return (
                        <th
                          aria-sort={active ? (sortDirection === "asc" ? "ascending" : "descending") : "none"}
                          className={`px-3 py-2 font-normal ${column.align === "right" ? "text-right" : ""}`}
                          key={column.key}
                        >
                          <button
                            className={`inline-flex min-h-8 items-center gap-1.5 rounded px-1 transition-colors hover:bg-stone-900 hover:text-stone-200 focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--accent)] ${column.align === "right" ? "justify-end" : ""} ${active ? "text-stone-200" : ""}`}
                            onClick={() => selectSort(column.key)}
                            type="button"
                          >
                            {column.label}
                            {active ? <span className="text-[9px] text-[var(--accent)]">{sortDirection}</span> : null}
                          </button>
                        </th>
                      );
                    })}
                  </tr>
                </thead>
                <tbody>
                  {visible.map((stock) => {
                    const expanded = expandedTicker === stock.ticker;
                    return (
                      <Fragment key={stock.ticker}>
                        <tr className="border-b border-stone-900 transition-colors hover:bg-stone-900/50">
                          <td className="px-3 py-4 font-mono text-xs text-stone-500">{number(stock.rank)}</td>
                          <td className="px-3 py-4">
                            <button
                              aria-expanded={expanded}
                              className="group block max-w-[260px] text-left focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--accent)]"
                              onClick={() => toggleDetails(stock.ticker)}
                              type="button"
                            >
                              <span className="flex items-center gap-3">
                                <span className="font-mono text-xs font-medium text-stone-100 group-hover:text-[var(--accent)]">{stock.ticker}</span>
                                {stock.data_quality?.status === "review" ? (
                                  <span className="border-l border-[var(--accent)] pl-2 font-mono text-[9px] uppercase tracking-[0.12em] text-[var(--accent)]">Review</span>
                                ) : null}
                              </span>
                              <span className="mt-1 block truncate text-xs text-stone-600">{stock.shortName}</span>
                              <span className="mt-1 block text-[10px] text-stone-700 group-hover:text-stone-500">{expanded ? "Hide details" : "View all data"}</span>
                            </button>
                          </td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-300">{currency(stock.currentPrice)}</td>
                          <td className="px-3 py-4 text-right font-mono text-sm font-medium text-stone-100">{number(stock.final_score)}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.data_cov, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.trailingPE)}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.priceToBook)}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.roe_pct, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.debtToEquity)}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.promoter_pct, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.institutional_pct, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{number(stock.pct_below_52w_high, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{signed(stock.upside_pct, "%")}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{crores(stock.mcap_cr)}</td>
                          <td className="px-3 py-4 text-right font-mono text-xs text-stone-400">{signed(stock.rank_vs_staged)}</td>
                        </tr>
                        {expanded ? (
                          <tr className="border-b border-stone-800" key={`${stock.ticker}-details`}>
                            <td colSpan={COLUMNS.length}><StockDetails stock={stock} /></td>
                          </tr>
                        ) : null}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>

            <div className="divide-y divide-stone-800 md:hidden">
              {visible.map((stock) => {
                const expanded = expandedTicker === stock.ticker;
                return (
                  <article className="py-5" key={stock.ticker}>
                    <button
                      aria-expanded={expanded}
                      className="block w-full text-left focus-visible:outline focus-visible:outline-1 focus-visible:outline-[var(--accent)]"
                      onClick={() => toggleDetails(stock.ticker)}
                      type="button"
                    >
                      <span className="flex items-start justify-between gap-4">
                        <span>
                          <span className="block font-mono text-sm font-medium text-stone-100">{stock.ticker}</span>
                          <span className="mt-1 block line-clamp-1 text-xs text-stone-600">{stock.shortName}</span>
                        </span>
                        <span className="text-right">
                          <span className="block font-mono text-sm text-stone-100">{number(stock.final_score)}</span>
                          <span className="mt-1 block font-mono text-[10px] uppercase tracking-[0.12em] text-stone-600">Score</span>
                        </span>
                      </span>
                      <span className="mt-5 grid grid-cols-4 gap-3">
                        {[
                          ["Rank", number(stock.rank)],
                          ["Price", currency(stock.currentPrice)],
                          ["ROE", number(stock.roe_pct, "%")],
                          ["Promoter", number(stock.promoter_pct, "%")],
                        ].map(([label, value]) => (
                          <span key={label}>
                            <span className="block font-mono text-[9px] uppercase tracking-[0.12em] text-stone-700">{label}</span>
                            <span className="mt-1 block truncate font-mono text-[11px] text-stone-400">{value}</span>
                          </span>
                        ))}
                      </span>
                      <span className="mt-4 block text-[11px] text-[var(--accent)]">{expanded ? "Hide details" : "View all data"}</span>
                    </button>
                    {expanded ? <StockDetails stock={stock} /> : null}
                  </article>
                );
              })}
            </div>

            <div className="mt-6 flex flex-col gap-3 border-t border-stone-800 pt-4 sm:flex-row sm:items-center sm:justify-between">
              <label className="flex items-center gap-2 text-xs text-stone-600">
                Rows
                <select
                  className="h-9 rounded-md border border-stone-800 bg-stone-900/50 px-2 text-xs text-stone-300 outline-none focus:border-[var(--accent)]"
                  onChange={(event) => {
                    setPageSize(Number(event.target.value));
                    setPage(1);
                  }}
                  value={pageSize}
                >
                  <option value={50}>50</option>
                  <option value={100}>100</option>
                  <option value={250}>250</option>
                  <option value={0}>All</option>
                </select>
              </label>
              <div className="flex items-center gap-3">
                <button
                  className="min-h-9 rounded-md border border-stone-800 px-3 text-xs text-stone-400 transition-colors hover:border-stone-600 hover:text-stone-200 disabled:cursor-not-allowed disabled:opacity-30 active:translate-y-px"
                  disabled={safePage <= 1}
                  onClick={() => setPage((current) => Math.max(1, current - 1))}
                  type="button"
                >
                  Previous
                </button>
                <span className="font-mono text-[11px] text-stone-600">Page {safePage} of {totalPages}</span>
                <button
                  className="min-h-9 rounded-md border border-stone-800 px-3 text-xs text-stone-400 transition-colors hover:border-stone-600 hover:text-stone-200 disabled:cursor-not-allowed disabled:opacity-30 active:translate-y-px"
                  disabled={safePage >= totalPages}
                  onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
                  type="button"
                >
                  Next
                </button>
              </div>
            </div>
          </>
        ) : (
          <div className="border-y border-stone-800 py-20 text-center">
            <p className="text-sm text-stone-300">
              {dashboard.data_status === "offline" ? "The dashboard API is unavailable." : "No stocks match these filters."}
            </p>
            {dashboard.data_status !== "offline" ? (
              <button
                className="mt-4 min-h-10 rounded-md border border-stone-700 px-4 text-xs text-stone-400 transition-colors hover:border-stone-500 hover:text-stone-200 active:translate-y-px"
                onClick={() => {
                  setQuery("");
                  setSector("all");
                  setMinimumCoverage(0);
                  setPage(1);
                }}
                type="button"
              >
                Reset filters
              </button>
            ) : null}
          </div>
        )}
      </section>
    </div>
  );
}
