"""전체 KOSDAQ 종목에 대해 우리 dividend_adjusted_prices와 참조파일(수정주가수익률)을
비교해 코너케이스를 찾는다. 읽기 전용 — DB에 아무것도 쓰지 않는다.

사용법: python scripts/validate_dividend_adjusted_full.py [xlsx_path]
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402

CODE_ROW = 7
NAME_ROW = 8
DATA_START_ROW = 13  # 13행이 컬럼헤더, 14행부터 데이터


def _clean_ticker(code: str) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def load_reference_long(path: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        names = raw.iloc[NAME_ROW, 1:].tolist()
        block = raw.iloc[DATA_START_ROW + 1 :, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name="ref_return")
        long = long.dropna(subset=["ref_return"])
        long["ref_return"] = long["ref_return"].astype(float)
        frames.append(long)
        print(f"  {sheet}: {len(codes)}개 종목, {len(long)}건")
    df = pd.concat(frames, ignore_index=True)
    name_map = {}
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=NAME_ROW + 1)
        for c, n in zip(raw.iloc[CODE_ROW, 1:], raw.iloc[NAME_ROW, 1:]):
            name_map[_clean_ticker(c)] = n
    return df, name_map


def load_ours_long(db) -> pd.DataFrame:
    rows = (
        db.query(Instrument.ticker, DividendAdjustedPrice.date, DividendAdjustedPrice.adj_close)
        .join(DividendAdjustedPrice, DividendAdjustedPrice.instrument_id == Instrument.id)
        .filter(DividendAdjustedPrice.period == "D")
        .all()
    )
    df = pd.DataFrame(rows, columns=["ticker", "date", "adj_close"])
    df["date"] = pd.to_datetime(df["date"])
    df["adj_close"] = df["adj_close"].astype(float)
    df = df.sort_values(["ticker", "date"])
    df["our_return"] = df.groupby("ticker")["adj_close"].pct_change() * 100
    return df.dropna(subset=["our_return"])[["ticker", "date", "our_return"]]


def main(path: str):
    print(f"reading {path} ...")
    ref_long, name_map = load_reference_long(path)
    ref_tickers = set(ref_long["ticker"].unique())
    print(f"참조파일 총 {len(ref_tickers)}개 종목, {len(ref_long)}건")

    db = SessionLocal()
    ours_long = load_ours_long(db)
    our_tickers = set(ours_long["ticker"].unique())
    print(f"우리 DB 총 {len(our_tickers)}개 종목(배당조정지수 보유), {len(ours_long)}건")

    only_in_ref = ref_tickers - our_tickers
    only_in_ours = our_tickers - ref_tickers
    print(f"참조파일에만 있음(우리 DB에 없음): {len(only_in_ref)}개")
    print(f"우리 DB에만 있음(참조파일에 없음): {len(only_in_ours)}개")

    common = ref_tickers & our_tickers
    print(f"비교 대상 공통 종목: {len(common)}개")

    merged = pd.merge(
        ours_long[ours_long["ticker"].isin(common)],
        ref_long[ref_long["ticker"].isin(common)],
        on=["ticker", "date"],
        how="inner",
    )

    rows = []
    for ticker, g in merged.groupby("ticker"):
        if len(g) < 30:
            continue
        diff = (g["our_return"] - g["ref_return"]).abs()
        corr = g["our_return"].corr(g["ref_return"])
        mae = diff.mean()
        max_diff = diff.max()
        max_diff_date = g.loc[diff.idxmax(), "date"]
        within = (diff <= 0.1).mean() * 100
        rows.append(
            dict(
                ticker=ticker,
                name=name_map.get(ticker, ""),
                n=len(g),
                corr=corr,
                mae=mae,
                max_diff=max_diff,
                max_diff_date=max_diff_date.date(),
                within_0_1pct=within,
            )
        )

    result = pd.DataFrame(rows).sort_values("corr")
    out_path = Path(__file__).resolve().parents[2] / "reference" / "validation_full_results.csv"
    result.to_csv(out_path, index=False, encoding="utf-8-sig")
    print(f"\n결과 {len(result)}개 종목 -> {out_path}")

    print("\n=== corr 분포 ===")
    bins = [(-1.01, 0.9, "<0.9"), (0.9, 0.95, "0.9~0.95"), (0.95, 0.99, "0.95~0.99"), (0.99, 1.01, ">=0.99")]
    for lo, hi, label in bins:
        cnt = ((result["corr"] > lo) & (result["corr"] <= hi)).sum()
        print(f"  {label}: {cnt}개")

    pd.set_option("display.width", 200)
    print("\n=== corr 낮은 순 상위 40개 ===")
    print(result.head(40).to_string(index=False))

    print("\n=== max_diff 높은 순 상위 40개 ===")
    print(result.sort_values("max_diff", ascending=False).head(40).to_string(index=False))

    if only_in_ref:
        print(f"\n=== 우리 DB에 없는 종목 샘플 (최대 20개, 총 {len(only_in_ref)}개) ===")
        print(sorted(only_in_ref)[:20])


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/kosdaq수정주가수익률.xlsx"
    main(target)
