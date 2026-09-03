-- One definition of "the current value of a field for a stock".
--
-- Two sources are live at once during the CSV migration:
--   * metric_observations — appended by providers, carries provenance and validation
--   * archive.csv_rows    — the legacy pipeline's output, imported verbatim
--
-- Both the API and the scoring job need the same answer to "what is this stock's ROE".
-- Putting that rule in apps/api would force packages/pipeline to import the web layer to
-- rank anything — a dependency pointing the wrong way — or to keep a second copy that
-- drifts from the first. So the rule lives here, in the schema, and every caller inherits
-- it: API, ranking job, ad-hoc SQL, anything added later.
--
-- Precedence: a valid live observation wins; newest wins within a provider tie; the
-- archive fills the rest. Invalid observations never win — they stay queryable for audit
-- but are not "current".

CREATE OR REPLACE VIEW current_metrics AS
WITH live AS (
    SELECT DISTINCT ON (o.instrument_id, o.field)
        o.instrument_id,
        o.field,
        o.numeric_value,
        o.text_value,
        o.unit,
        o.provider,
        o.observed_at,
        'observation'::text AS origin
    FROM metric_observations AS o
    WHERE o.is_valid
    ORDER BY o.instrument_id, o.field, o.observed_at DESC, o.id DESC
),
-- The newest import of each archived file, so a re-import supersedes rather than duplicates.
newest_files AS (
    SELECT DISTINCT ON (source_path) source_path, content_sha256, source_modified_at
    FROM archive.csv_files
    ORDER BY source_path, source_modified_at DESC, imported_at DESC
),
archived AS (
    SELECT DISTINCT ON (i.id, kv.key)
        i.id AS instrument_id,
        kv.key AS field,
        -- Only keep values that are actually numeric; blanks and text stay in text_value.
        CASE WHEN kv.value #>> '{}' ~ '^-?[0-9]+\.?[0-9]*([eE][-+]?[0-9]+)?$'
             THEN (kv.value #>> '{}')::numeric END AS numeric_value,
        NULLIF(kv.value #>> '{}', '') AS text_value,
        NULL::text AS unit,
        'legacy_csv'::text AS provider,
        f.source_modified_at AS observed_at,
        'archive'::text AS origin
    FROM newest_files AS f
    JOIN archive.csv_rows AS r
      ON r.source_path = f.source_path AND r.content_sha256 = f.content_sha256
    CROSS JOIN LATERAL jsonb_each(r.record) AS kv(key, value)
    JOIN instruments AS i
      ON i.ticker = upper(btrim(r.record ->> 'ticker'))
    WHERE r.record ? 'ticker'
      AND kv.key <> 'ticker'
      AND NULLIF(kv.value #>> '{}', '') IS NOT NULL
    ORDER BY i.id, kv.key, f.source_modified_at DESC
)
SELECT
    COALESCE(live.instrument_id, archived.instrument_id) AS instrument_id,
    COALESCE(live.field, archived.field)                 AS field,
    COALESCE(live.numeric_value, archived.numeric_value)  AS numeric_value,
    COALESCE(live.text_value, archived.text_value)        AS text_value,
    COALESCE(live.unit, archived.unit)                    AS unit,
    COALESCE(live.provider, archived.provider)            AS provider,
    COALESCE(live.observed_at, archived.observed_at)      AS observed_at,
    COALESCE(live.origin, archived.origin)                AS origin
FROM live
FULL OUTER JOIN archived
  ON archived.instrument_id = live.instrument_id AND archived.field = live.field;

COMMENT ON VIEW current_metrics IS
    'Current value per (instrument, field): valid live observation preferred, newest '
    'archived CSV as fallback. Single source of precedence for the API and the ranking job.';
