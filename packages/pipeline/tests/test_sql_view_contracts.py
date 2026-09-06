from __future__ import annotations

from find_next_pipeline.paths import ROOT


def _latest_migration_defining(view: str) -> str:
    marker = f"CREATE OR REPLACE VIEW {view}"
    candidates = [
        path
        for path in sorted((ROOT / "infra" / "db" / "init").glob("*.sql"))
        if marker in path.read_text(encoding="utf-8")
    ]
    assert candidates, f"no migration defines {view}"
    return candidates[-1].read_text(encoding="utf-8")


def test_latest_current_metrics_keeps_every_fail_closed_rule() -> None:
    """A newer view migration must not resurrect data rejected by an older rule."""
    sql = _latest_migration_defining("current_metrics")

    assert "stock_instrument_symbols" in sql
    assert "instrument_id IN (SELECT id FROM stock_instruments)" in sql
    assert "instrument_id IN (SELECT instrument_id FROM pe_unusable)" in sql
    assert "instrument_id IN (SELECT instrument_id FROM roe_unusable)" in sql


def test_latest_stock_universe_is_active_equities_only() -> None:
    sql = _latest_migration_defining("stock_instruments")

    assert "kind = 'EQUITY' AND active" in sql
