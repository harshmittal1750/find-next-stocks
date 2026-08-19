# Tracked normalized snapshots

`dashboard-data.json` is the validated, UI-ready research snapshot. It is intentionally tracked
in Git so changes to scores and metrics are reviewable independently from the dashboard code.
Rebuild it with `make import-legacy` while the migration is in progress; the new provider pipeline
will replace that importer incrementally.
