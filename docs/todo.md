# Remaining data work

- **Statement coverage:** complete the Upstox backfill and measure missing consolidated
  filings. Capex, free cash flow, detailed debt, and standalone-only companies still need
  a verified source and consistent accounting policy.
- **Ranking coverage:** fill verified quality and valuation inputs before stocks qualify
  for ranking. Upstox's four-quarter sample lacks the fifth quarter needed for annual
  earnings growth comparisons; keep the reported amounts without inventing that growth.
- **Analyst coverage:** forward estimates, targets, recommendations, and analyst counts
  exist only for covered stocks. Distinguish unavailable coverage from fetch failures.
- **Institution counts:** obtain a shareholding-pattern filing source. Yahoo holder
  percentages do not provide a count, and insider ownership is only a promoter proxy.
- **P/E validation:** the exchange-disagreement guard exists. A price/EPS cross-check
  still needs an accounting-basis-aware policy to avoid rejecting valid ratios.
- **Screener access:** retry unresolved ROE reviews when source access allows it. Keep
  requests paced and use the trusted Upstox return-ratio fallback where available.
- **Archive retirement:** replace remaining reference fields before removing CSV fallback.
  Scoring already excludes archived inputs; preserve the original byte archive afterward.

Measure current archive dependence instead of keeping stale counts in documentation:

```sql
SELECT origin, count(*) AS cells, count(DISTINCT field) AS fields
FROM current_metrics
GROUP BY origin;
```
