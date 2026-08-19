from pathlib import Path

from find_next_pipeline.csv_archive import read_csv_file


def test_csv_archive_preserves_exact_bytes_and_ordered_cells(tmp_path: Path) -> None:
    content = 'ticker,note,value\r\nGALLANTT,"line one\nline two",70.0\r\n'
    source = tmp_path / "sample.csv"
    source.write_bytes(content.encode("utf-8"))

    archived = read_csv_file(source, root=tmp_path)

    assert archived.content == content.encode("utf-8")
    assert archived.header == ["ticker", "note", "value"]
    assert archived.row_count == 1
    assert archived.rows[0].values == ["GALLANTT", "line one\nline two", "70.0"]
    assert archived.rows[0].record["ticker"] == "GALLANTT"


def test_csv_archive_keeps_extra_and_missing_columns(tmp_path: Path) -> None:
    source = tmp_path / "uneven.csv"
    source.write_text("a,b\n1\n2,3,4\n", encoding="utf-8")

    archived = read_csv_file(source, root=tmp_path)

    assert archived.rows[0].values == ["1"]
    assert archived.rows[0].record == {"a": "1", "b": None}
    assert archived.rows[1].values == ["2", "3", "4"]
    assert archived.rows[1].record["extra_column_1"] == "4"
