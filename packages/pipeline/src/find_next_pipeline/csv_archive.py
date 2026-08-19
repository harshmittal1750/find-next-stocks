from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from find_next_pipeline.paths import ROOT

IGNORED_PARTS = {".git", ".next", ".venv", "node_modules"}
DDL_PATH = ROOT / "infra" / "db" / "init" / "002_csv_archive.sql"
MANIFEST_DIR = ROOT / "data" / "migrations"
DEFAULT_BATCH_SIZE = 500


@dataclass(frozen=True)
class ArchivedRow:
    row_number: int
    values: list[str]
    record: dict[str, Any]
    sha256: str


@dataclass(frozen=True)
class CsvFile:
    path: Path
    source_path: str
    content: bytes
    sha256: str
    size_bytes: int
    source_modified_at: datetime
    header: list[str]
    rows: list[ArchivedRow]

    @property
    def row_count(self) -> int:
        return len(self.rows)


def discover_csv_files(root: Path = ROOT) -> list[Path]:
    return sorted(
        path
        for path in root.rglob("*.csv")
        if path.is_file()
        and not any(part in IGNORED_PARTS for part in path.relative_to(root).parts)
    )


def _record_from_row(header: list[str], values: list[str]) -> dict[str, Any]:
    record: dict[str, Any] = {}
    duplicate_counts: dict[str, int] = {}
    for index, value in enumerate(values):
        if index < len(header):
            base_key = header[index] or f"column_{index + 1}"
        else:
            base_key = f"extra_column_{index - len(header) + 1}"
        duplicate_counts[base_key] = duplicate_counts.get(base_key, 0) + 1
        suffix = duplicate_counts[base_key]
        key = base_key if suffix == 1 else f"{base_key}__duplicate_{suffix}"
        record[key] = value
    for index in range(len(values), len(header)):
        base_key = header[index] or f"column_{index + 1}"
        duplicate_counts[base_key] = duplicate_counts.get(base_key, 0) + 1
        suffix = duplicate_counts[base_key]
        key = base_key if suffix == 1 else f"{base_key}__duplicate_{suffix}"
        record[key] = None
    return record


def read_csv_file(path: Path, root: Path = ROOT) -> CsvFile:
    content = path.read_bytes()
    text = content.decode("utf-8-sig")
    reader = csv.reader(io.StringIO(text, newline=""))
    header = next(reader, [])
    rows: list[ArchivedRow] = []
    for row_number, values in enumerate(reader, start=1):
        canonical = json.dumps(values, ensure_ascii=False, separators=(",", ":"))
        rows.append(
            ArchivedRow(
                row_number=row_number,
                values=values,
                record=_record_from_row(header, values),
                sha256=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
            )
        )
    return CsvFile(
        path=path,
        source_path=path.relative_to(root).as_posix(),
        content=content,
        sha256=hashlib.sha256(content).hexdigest(),
        size_bytes=len(content),
        source_modified_at=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
        header=header,
        rows=rows,
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _sql_json(value: Any) -> str:
    serialized = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return f"{_sql_string(serialized)}::jsonb"


def _psql(*, input_sql: str, tuples_only: bool = False) -> str:
    psql_flags = "-X -v ON_ERROR_STOP=1"
    if tuples_only:
        psql_flags += " -A -t"
    command = [
        "docker",
        "compose",
        "exec",
        "-T",
        "timescaledb",
        "sh",
        "-lc",
        f'psql {psql_flags} -U "$POSTGRES_USER" -d "$POSTGRES_DB"',
    ]
    result = subprocess.run(
        command,
        cwd=ROOT,
        input=input_sql,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()


def _insert_rows_sql(file: CsvFile, batch_size: int) -> list[str]:
    statements = [
        "DELETE FROM archive.csv_rows "
        f"WHERE source_path = {_sql_string(file.source_path)} "
        f"AND content_sha256 = {_sql_string(file.sha256)};"
    ]
    for start in range(0, file.row_count, batch_size):
        values = []
        for row in file.rows[start : start + batch_size]:
            values.append(
                "("
                f"{_sql_string(file.source_path)},"
                f"{_sql_string(file.sha256)},"
                f"{row.row_number},"
                f"{_sql_string(row.sha256)},"
                f"{_sql_json(row.values)},"
                f"{_sql_json(row.record)}"
                ")"
            )
        statements.append(
            "INSERT INTO archive.csv_rows "
            "(source_path, content_sha256, row_number, row_sha256, values_array, record) VALUES\n"
            + ",\n".join(values)
            + ";"
        )
    return statements


def build_import_sql(
    files: list[CsvFile], migration_id: UUID, batch_size: int = DEFAULT_BATCH_SIZE
) -> str:
    statements = [DDL_PATH.read_text(encoding="utf-8"), "BEGIN;"]
    for file in files:
        statements.append(
            "INSERT INTO archive.csv_files "
            "(source_path, content_sha256, migration_id, size_bytes, source_modified_at, "
            "header, row_count, original_csv) VALUES ("
            f"{_sql_string(file.source_path)},"
            f"{_sql_string(file.sha256)},"
            f"{_sql_string(str(migration_id))}::uuid,"
            f"{file.size_bytes},"
            f"{_sql_string(file.source_modified_at.isoformat())}::timestamptz,"
            f"{_sql_json(file.header)},"
            f"{file.row_count},"
            f"decode({_sql_string(file.content.hex())}, 'hex')"
            ") ON CONFLICT (source_path, content_sha256) DO UPDATE SET "
            "migration_id = EXCLUDED.migration_id, "
            "size_bytes = EXCLUDED.size_bytes, "
            "source_modified_at = EXCLUDED.source_modified_at, "
            "header = EXCLUDED.header, "
            "row_count = EXCLUDED.row_count, "
            "original_csv = EXCLUDED.original_csv, "
            "imported_at = now();"
        )
        statements.extend(_insert_rows_sql(file, batch_size))
    statements.append("COMMIT;")
    return "\n".join(statements)


def fetch_database_audit(migration_id: UUID) -> list[dict[str, Any]]:
    query = (
        "SELECT COALESCE(json_agg(row_to_json(a) ORDER BY source_path), '[]'::json)::text "
        "FROM archive.csv_import_audit AS a "
        f"WHERE migration_id = {_sql_string(str(migration_id))}::uuid;"
    )
    output = _psql(input_sql=query, tuples_only=True)
    return json.loads(output or "[]")


def verify_import(files: list[CsvFile], audit: list[dict[str, Any]]) -> list[str]:
    expected = {file.source_path: file for file in files}
    actual = {row["source_path"]: row for row in audit}
    errors: list[str] = []
    if set(expected) != set(actual):
        missing = sorted(set(expected) - set(actual))
        unexpected = sorted(set(actual) - set(expected))
        if missing:
            errors.append(f"missing database files: {', '.join(missing)}")
        if unexpected:
            errors.append(f"unexpected database files: {', '.join(unexpected)}")
    for source_path, file in expected.items():
        row = actual.get(source_path)
        if row is None:
            continue
        checks = {
            "declared_sha256": file.sha256,
            "stored_sha256": file.sha256,
            "declared_size_bytes": file.size_bytes,
            "stored_size_bytes": file.size_bytes,
            "declared_row_count": file.row_count,
            "stored_row_count": file.row_count,
            "header": file.header,
            "verified": True,
        }
        for key, wanted in checks.items():
            if row.get(key) != wanted:
                errors.append(
                    f"{source_path}: {key} expected {wanted!r}, received {row.get(key)!r}"
                )
    return errors


def write_manifest(
    files: list[CsvFile], migration_id: UUID, audit: list[dict[str, Any]], deleted: bool
) -> Path:
    MANIFEST_DIR.mkdir(parents=True, exist_ok=True)
    target = MANIFEST_DIR / f"csv-to-postgres-{datetime.now(UTC).date().isoformat()}.json"
    payload = {
        "schema_version": 1,
        "migration_id": str(migration_id),
        "created_at": datetime.now(UTC).isoformat(),
        "database_schema": "archive",
        "database_files_table": "archive.csv_files",
        "database_rows_table": "archive.csv_rows",
        "source_files_deleted_after_verification": deleted,
        "file_count": len(files),
        "total_rows": sum(file.row_count for file in files),
        "total_bytes": sum(file.size_bytes for file in files),
        "files": [
            {
                "source_path": file.source_path,
                "sha256": file.sha256,
                "size_bytes": file.size_bytes,
                "row_count": file.row_count,
                "column_count": len(file.header),
                "header": file.header,
            }
            for file in files
        ],
        "database_audit": audit,
    }
    target.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return target


def migrate(*, delete_after_verify: bool, batch_size: int = DEFAULT_BATCH_SIZE) -> Path | None:
    paths = discover_csv_files()
    if not paths:
        print("No project CSV files found. Dependency-owned CSVs under .venv are excluded.")
        return None
    files = [read_csv_file(path) for path in paths]
    migration_id = uuid4()
    print(
        f"Archiving {len(files)} CSV files, {sum(file.row_count for file in files)} rows, "
        f"and {sum(file.size_bytes for file in files)} exact source bytes"
    )
    _psql(input_sql=build_import_sql(files, migration_id, batch_size))
    audit = fetch_database_audit(migration_id)
    errors = verify_import(files, audit)
    if errors:
        raise RuntimeError("Database verification failed:\n" + "\n".join(errors))

    manifest = write_manifest(files, migration_id, audit, delete_after_verify)
    if delete_after_verify:
        for file in files:
            file.path.unlink()
        print(f"Verified and deleted {len(files)} source CSV files")
    else:
        print("Verified database import; source CSV files retained")
    print(f"Audit manifest: {manifest.relative_to(ROOT)}")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archive every project CSV into PostgreSQL and verify it losslessly."
    )
    parser.add_argument(
        "--delete-after-verify",
        action="store_true",
        help="Delete source CSVs only after database hashes, sizes, headers, and rows match.",
    )
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args()
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    migrate(delete_after_verify=args.delete_after_verify, batch_size=args.batch_size)


if __name__ == "__main__":
    main()
