"""Build the shareholding layer without a second Yahoo request per stock.

``screen_universe.py`` already receives Yahoo's insider and institutional-held
fractions inside ``Ticker.info``. The old stage fetched the same Yahoo source
again through ``major_holders`` for all 1,353 stocks. This version reuses the
raw fields, rejects impossible percentages, applies traceable official-source
overrides, and retains the last known institution count in a quarterly cache.
"""
import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")
import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
CACHE = DATA / "cache" / "shareholding_snapshot.csv"
OUTPUT = DATA / "shareholding_layer.csv"
OVERRIDES = DATA / "shareholding_overrides.json"


def atomic_csv(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(path)


def valid_percent(values):
    """Return numeric percentages only when they fit the physical 0..100 range."""
    numeric = pd.to_numeric(values, errors="coerce")
    return numeric.where(numeric.between(0, 100, inclusive="both"))


def load_overrides():
    if not OVERRIDES.exists():
        return {}
    payload = json.loads(OVERRIDES.read_text(encoding="utf-8"))
    if payload.get("schema_version") != 1 or not isinstance(payload.get("stocks"), dict):
        raise ValueError(f"invalid shareholding override schema in {OVERRIDES}")
    return payload["stocks"]


def cached_snapshot():
    """Prefer the local cache; bootstrap it from the previous stage output."""
    for path in [CACHE, OUTPUT]:
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path)
            keep = [col for col in ["ticker", "promoter_pct", "institutional_pct", "institutions_count"]
                    if col in frame]
            if "ticker" in keep:
                frame = frame[keep].drop_duplicates("ticker", keep="last")
                for column in ["promoter_pct", "institutional_pct"]:
                    if column in frame:
                        frame[column] = valid_percent(frame[column])
                return frame
        except Exception:
            continue
    return pd.DataFrame(columns=["ticker", "promoter_pct", "institutional_pct", "institutions_count"])


def raw_ownership():
    raw = pd.read_csv(DATA / "raw_fundamentals.csv")
    out = pd.DataFrame({"ticker": raw["ticker"].astype(str)})
    insiders_col = raw["heldPercentInsiders"] if "heldPercentInsiders" in raw else pd.Series(pd.NA, index=raw.index)
    institutions_col = raw["heldPercentInstitutions"] if "heldPercentInstitutions" in raw else pd.Series(pd.NA, index=raw.index)
    insiders = pd.to_numeric(insiders_col, errors="coerce")
    institutions = pd.to_numeric(institutions_col, errors="coerce")
    # Yahoo returns fractions here. A value such as 1.07481 means 107.481%, not
    # 1.07481%; it is an upstream data error and must not enter the ranking.
    out["promoter_pct_raw"] = valid_percent(insiders * 100).round(1)
    out["institutional_pct_raw"] = valid_percent(institutions * 100).round(1)
    out["promoter_pct_rejected"] = insiders.notna() & out["promoter_pct_raw"].isna()
    out["institutional_pct_rejected"] = institutions.notna() & out["institutional_pct_raw"].isna()
    return out.drop_duplicates("ticker", keep="last")


def build_layer(base, raw, cached, overrides):
    frame = base.merge(raw, on="ticker", how="left")
    frame = frame.merge(cached, on="ticker", how="left")
    frame["promoter_pct"] = frame["promoter_pct_raw"].combine_first(frame.get("promoter_pct"))
    frame["institutional_pct"] = frame["institutional_pct_raw"].combine_first(frame.get("institutional_pct"))
    if "institutions_count" not in frame:
        frame["institutions_count"] = pd.NA

    for ticker, override in overrides.items():
        mask = frame["ticker"].astype(str).eq(str(ticker))
        if not mask.any():
            continue
        for field in ["promoter_pct", "institutional_pct", "institutions_count"]:
            if field not in override:
                continue
            value = pd.to_numeric(override[field], errors="coerce")
            if pd.isna(value) or (field != "institutions_count" and not 0 <= value <= 100):
                raise ValueError(f"invalid {field} override for {ticker}: {override[field]}")
            frame.loc[mask, field] = value

    # In this India-specific dashboard promoter and public institutional buckets
    # are treated as disjoint. If a stale Yahoo pair exceeds 100%, retain the
    # promoter observation and leave institutional ownership unknown.
    combined = (pd.to_numeric(frame["promoter_pct"], errors="coerce") +
                pd.to_numeric(frame["institutional_pct"], errors="coerce"))
    frame.loc[combined > 100.5, "institutional_pct"] = pd.NA

    scores, reasons = [], []
    for row in frame.itertuples(index=False):
        score, why = 0, []
        promoter = getattr(row, "promoter_pct", None)
        institutional = getattr(row, "institutional_pct", None)
        count = getattr(row, "institutions_count", None)
        if promoter is not None and pd.notna(promoter):
            if promoter >= 50: score += 2; why.append(f"strong promoter {promoter}%")
            elif promoter >= 40: score += 1; why.append(f"promoter {promoter}%")
            if promoter >= 75: why.append("⚠ very high promoter = low float")
        if institutional is not None and pd.notna(institutional):
            if institutional >= 40: score += 2; why.append(f"high institutional {institutional}%")
            elif institutional >= 25: score += 1; why.append(f"institutional {institutional}%")
        if count is not None and pd.notna(count) and count >= 250:
            score += 1; why.append(f"{int(count)} institutions")
        scores.append(score)
        reasons.append("; ".join(why))
    frame["shareholding_score"] = scores
    frame["shareholding_reasons"] = reasons
    combined = pd.to_numeric(frame.get("combined_score", frame.get("score")), errors="coerce").fillna(0)
    frame["final_score"] = combined + frame["shareholding_score"]
    return frame.sort_values("final_score", ascending=False)


def main():
    base = pd.read_csv(DATA / "ownership_overlay.csv")
    cached = cached_snapshot()
    raw = raw_ownership()
    overrides = load_overrides()
    out = build_layer(base, raw, cached, overrides)

    snapshot = out[["ticker", "promoter_pct", "institutional_pct", "institutions_count"]]
    atomic_csv(snapshot, CACHE)
    keep = ["ticker", "shortName", "sector", "currentPrice", "pct_below_52w_high", "trailingPE",
            "roe_pct", "debtToEquity", "promoter_pct", "institutional_pct", "institutions_count",
            "avg_delivery_pct", "delivery_trend", "score", "ownership_score", "shareholding_score",
            "final_score", "reasons", "shareholding_reasons"]
    keep = [col for col in keep if col in out.columns]
    atomic_csv(out[keep], OUTPUT)
    raw_coverage = int(out["promoter_pct_raw"].notna().sum())
    rejected = out.loc[out["promoter_pct_rejected"].fillna(False), "ticker"].astype(str).tolist()
    print(f"Saved -> {OUTPUT}")
    print(f"Shareholding reused from raw fundamentals: {raw_coverage}/{len(out)} stocks; "
          "institution counts retained from quarterly cache")
    if rejected:
        print(f"Rejected impossible Yahoo promoter percentages: {', '.join(rejected)}")
    if overrides:
        print(f"Applied official-source overrides: {', '.join(sorted(overrides))}")


if __name__ == "__main__":
    main()
