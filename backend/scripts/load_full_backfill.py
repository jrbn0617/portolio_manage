"""전체종목(코스피+코스닥, 2020년 이후 한 번이라도 편입된 전체) 대상 가격+상장주식수
재적재. reference/백필1.xlsx ~ 백필N.xlsx (파일당 ~500종목, 티커 그룹별로 쪼개짐) 을
전부 읽어 적재한다.

파일 형식: 파일 하나에 시트 6개 — 종가(raw_close)/수정시가(open)/수정고가(high)/
수정저가(low)/수정종가(close)/상장주식수. 백필1.xlsx만 시트명에 "1" 접미사가 붙어있고
나머지는 접미사 없음 — 둘 다 처리한다. 상장주식수는 일단위로 와서(대부분 종목이
값이 안 바뀌는 날이 훨씬 많음) 종목별로 값이 실제로 바뀌는 날짜만 남기고 적재한다
(monthly_fundamentals는 "기준일 이하 최신값" 조회 방식이라 중간에 안 바뀐 날짜를
다 넣을 필요가 없음).

사용법: python scripts/load_full_backfill.py [파일1 파일2 ...]
        (인자 없으면 ../reference/백필*.xlsx 전부)
"""
import glob
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.market_holiday import MarketHoliday
from app.models.monthly_fundamental import MonthlyFundamental
from app.models.price import Price

CODE_ROW = 8
NAME_ROW = 9
DATA_START_ROW = 15

PRICE_FIELD_MAP = {"종가": "raw_close", "수정시가": "open", "수정고가": "high", "수정저가": "low", "수정종가": "close"}
SHARES_SHEET = "상장주식수"


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _sheet_names(path: str) -> dict[str, str]:
    """이 파일에서 각 필드가 어느 시트에 있는지("1" 접미사 유무 대응)."""
    xl = pd.ExcelFile(path)
    names = {}
    for base in list(PRICE_FIELD_MAP) + [SHARES_SHEET]:
        if base in xl.sheet_names:
            names[base] = base
        elif f"{base}1" in xl.sheet_names:
            names[base] = f"{base}1"
        else:
            raise ValueError(f"{path}: 시트 '{base}' 못 찾음 (실제: {xl.sheet_names})")
    return names


def _load_sheet_long(path: str, sheet: str, column_name: str) -> pd.DataFrame:
    raw = pd.read_excel(path, sheet_name=sheet, header=None)
    codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW - 1, 1:]]
    block = raw.iloc[DATA_START_ROW - 1 :, :].copy()
    block.columns = ["date"] + codes
    block["date"] = pd.to_datetime(block["date"], errors="coerce")
    block = block.dropna(subset=["date"])
    long = block.melt(id_vars="date", var_name="ticker", value_name=column_name)
    long["date"] = long["date"].dt.date
    return long


def load_one_file(path: str, db, instruments_by_ticker: dict[str, int], holidays: set):
    print(f"\n=== {path} ===")
    sheets = _sheet_names(path)

    frames = []
    for field, colname in PRICE_FIELD_MAP.items():
        df = _load_sheet_long(path, sheets[field], colname)
        df = df.dropna(subset=[colname])
        frames.append(df)

    merged = frames[0]
    for df in frames[1:]:
        merged = merged.merge(df, on=["ticker", "date"], how="outer")
    merged = merged.dropna(subset=["close"])
    merged = merged[~merged["date"].apply(lambda d: d.weekday() >= 5 or d in holidays)]
    merged = merged.drop_duplicates(subset=["ticker", "date"], keep="first")

    unknown = set(merged["ticker"].unique()) - set(instruments_by_ticker)
    if unknown:
        print(f"  경고: instruments에 없는 티커 {len(unknown)}개 — 건너뜀: {sorted(unknown)[:10]}...")
        merged = merged[~merged["ticker"].isin(unknown)]

    price_rows = []
    for r in merged.itertuples(index=False):
        row = {"instrument_id": instruments_by_ticker[r.ticker], "date": r.date, "period": "D"}
        for field in ("open", "high", "low", "close", "raw_close"):
            v = getattr(r, field, None)
            if pd.notna(v):
                row[field] = float(v)
        price_rows.append(row)

    print(f"  가격 {len(price_rows)}행 upsert 중...")
    batch_size = 5000
    for i in range(0, len(price_rows), batch_size):
        batch = price_rows[i : i + batch_size]
        stmt = pg_insert(Price).values(batch)
        set_cols = {c: getattr(stmt.excluded, c) for c in ("open", "high", "low", "close", "raw_close")}
        stmt = stmt.on_conflict_do_update(index_elements=["instrument_id", "date", "period"], set_=set_cols)
        db.execute(stmt)
    db.commit()
    print(f"  가격 완료 ({merged['date'].min()} ~ {merged['date'].max()})")

    shares_long = _load_sheet_long(path, sheets[SHARES_SHEET], "value")
    shares_long = shares_long.dropna(subset=["value"])
    shares_long = shares_long[shares_long["ticker"].isin(instruments_by_ticker)]
    shares_long = shares_long.sort_values(["ticker", "date"])
    shares_long["changed"] = shares_long.groupby("ticker")["value"].diff().ne(0)
    shares_long.loc[shares_long.groupby("ticker").cumcount() == 0, "changed"] = True
    changed = shares_long[shares_long["changed"]]

    fund_rows = [
        dict(instrument_id=instruments_by_ticker[r.ticker], date=r.date, metric="shares_outstanding_monthly", value=float(r.value))
        for r in changed.itertuples(index=False)
    ]
    print(f"  상장주식수 변경시점 {len(fund_rows)}행(전체 {len(shares_long)}행 중) upsert 중...")
    for i in range(0, len(fund_rows), batch_size):
        batch = fund_rows[i : i + batch_size]
        stmt = pg_insert(MonthlyFundamental).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
    db.commit()
    print("  상장주식수 완료")


def main(paths: list[str]):
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}

    for path in paths:
        load_one_file(path, db, instruments_by_ticker, holidays)

    db.close()
    print("\n전체 완료")


if __name__ == "__main__":
    targets = sys.argv[1:] if len(sys.argv) > 1 else sorted(glob.glob("../reference/백필[0-9]*.xlsx"))
    if not targets:
        print("대상 파일 없음")
        sys.exit(1)
    print(f"대상 파일 {len(targets)}개: {targets}")
    main(targets)
