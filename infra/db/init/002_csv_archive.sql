CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE SCHEMA IF NOT EXISTS archive;

CREATE TABLE IF NOT EXISTS archive.csv_files (
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
    migration_id UUID NOT NULL,
    size_bytes BIGINT NOT NULL CHECK (size_bytes >= 0),
    source_modified_at TIMESTAMPTZ NOT NULL,
    header JSONB NOT NULL CHECK (jsonb_typeof(header) = 'array'),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    original_csv BYTEA NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_path, content_sha256)
);

CREATE TABLE IF NOT EXISTS archive.csv_rows (
    source_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    row_number INTEGER NOT NULL CHECK (row_number >= 1),
    row_sha256 TEXT NOT NULL CHECK (length(row_sha256) = 64),
    values_array JSONB NOT NULL CHECK (jsonb_typeof(values_array) = 'array'),
    record JSONB NOT NULL CHECK (jsonb_typeof(record) = 'object'),
    PRIMARY KEY (source_path, content_sha256, row_number),
    FOREIGN KEY (source_path, content_sha256)
        REFERENCES archive.csv_files (source_path, content_sha256)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS csv_rows_record_gin
    ON archive.csv_rows USING GIN (record);

CREATE OR REPLACE VIEW archive.csv_import_audit AS
SELECT
    files.source_path,
    files.content_sha256 AS declared_sha256,
    encode(digest(files.original_csv, 'sha256'), 'hex') AS stored_sha256,
    files.size_bytes AS declared_size_bytes,
    octet_length(files.original_csv) AS stored_size_bytes,
    files.row_count AS declared_row_count,
    count(rows.row_number)::INTEGER AS stored_row_count,
    files.header,
    files.migration_id,
    files.imported_at,
    (
        files.content_sha256 = encode(digest(files.original_csv, 'sha256'), 'hex')
        AND files.size_bytes = octet_length(files.original_csv)
        AND files.row_count = count(rows.row_number)
    ) AS verified
FROM archive.csv_files AS files
LEFT JOIN archive.csv_rows AS rows
    ON rows.source_path = files.source_path
    AND rows.content_sha256 = files.content_sha256
GROUP BY
    files.source_path,
    files.content_sha256,
    files.size_bytes,
    files.row_count,
    files.header,
    files.migration_id,
    files.imported_at,
    files.original_csv;

COMMENT ON TABLE archive.csv_files IS
    'Exact original CSV bytes plus a file-level audit manifest. Rows are queryable in archive.csv_rows.';
COMMENT ON TABLE archive.csv_rows IS
    'Lossless ordered cell arrays and convenient header-keyed JSON objects for each archived CSV row.';
