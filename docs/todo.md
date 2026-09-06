# Remaining data work

- **Financial statements:** replace archive-only balance-sheet, income-statement, and
  cash-flow fields with a reachable provider. Alpha Vantage requires a pipeline API key
  and a rate-limit plan; an MCP connection does not supply that credential.
- **Analyst coverage:** forward estimates, targets, recommendations, and analyst counts
  exist only for covered stocks. Distinguish unavailable coverage from fetch failures.
- **Institution counts:** obtain a shareholding-pattern filing source. Yahoo holder
  percentages do not provide a count, and insider ownership is only a promoter proxy.
- **P/E validation:** the exchange-disagreement guard exists. A price/EPS cross-check
  still needs an accounting-basis-aware policy to avoid rejecting valid ratios.
- **Screener access:** retry unresolved ROE reviews when source access allows it. Keep
  requests paced and use the trusted Upstox return-ratio fallback where available.
- **Archive retirement:** replace remaining scoring inputs before removing CSV fallback.
  Preserve the original byte archive and provenance after migration.

Measure current archive dependence instead of keeping stale counts in documentation:

```sql
SELECT origin, count(*) AS cells, count(DISTINCT field) AS fields
FROM current_metrics
GROUP BY origin;
```
