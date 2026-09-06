-- Re-validate stored P/E observations against the rule normalization.py now applies.
--
-- `metric_observations` is append-only for *raw* values, and this does not change one:
-- every numeric_value stays exactly as the provider sent it, and the untouched HTTP body
-- is still on disk under data/raw/. What changes is `is_valid` / `validation_issues`,
-- which are derived state -- a verdict this codebase reached with the rules it had at
-- the time. When a rule is corrected, the verdicts have to be recomputed, or the fix
-- only ever applies to stocks fetched after it.
--
-- Skipping this step is a mistake this repo has already made once: 5c-2b-fix flagged
-- standalone-basis rows invalid and nothing happened, because `live_metrics` picks the
-- newest *valid* row and the older valid ones simply kept winning. Re-running the
-- provider does not help for the same reason -- a new invalid row does not displace an
-- old valid one.
--
-- Rule: a P/E above 300 means earnings rounded to nothing (GODAVARIB, consolidated EPS
-- 0.04, "P/E" 5550.15). One-sided on purpose -- a low P/E is a value signal, not an
-- error. See MAX_MEANINGFUL_PE in normalization.py, which is the source of truth; this
-- file only brings history into line with it.

UPDATE metric_observations
SET is_valid = false,
    validation_issues = validation_issues || jsonb_build_array(jsonb_build_object(
        'code', 'implausible_pe',
        'message', 'trailing_pe resolved to ' || round(numeric_value, 1)::text ||
                   '; above 300 the earnings are ~0 and the ratio carries no valuation signal',
        'field', field,
        'raw_value', numeric_value,
        'severity', 'error'
    ))
WHERE field IN ('trailing_pe', 'forward_pe')
  AND numeric_value > 300
  AND is_valid
  AND NOT validation_issues @> '[{"code": "implausible_pe"}]'::jsonb;
