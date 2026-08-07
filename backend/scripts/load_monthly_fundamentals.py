"""월 단위 종목 펀더멘털(유동비율, 상장주식수 등)을 WISEfn 벌크 엑셀에서
monthly_fundamentals 테이블로 적재한다. metric 인자로 항목을 구분해서
같은 스크립트로 앞으로 추가될 다른 월간 지표도 그대로 적재할 수 있다.

파일 형식: 여러 시트("1","2","3"...)로 나뉜 단일필드 월간 시계열.
3행=Frequency(M), 7행=Code, 8행=Name, 13행=필드라벨, 14행부터 데이터.

사용법:
  python scripts/load_monthly_fundamentals.py "../reference/유동비율.xlsx" free_float_ratio
  python scripts/load_monthly_fundamentals.py "../reference/상장주식수.xlsx" shares_outstanding_monthly
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental

CODE_ROW = 7
NAME_ROW = 8
DATA_START_ROW = 14


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _load_all_sheets_long(path: str, column_name: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        block = raw.iloc[DATA_START_ROW:, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name=column_name)
        long = long.dropna(subset=[column_name])
        frames.append(long)
    return pd.concat(frames, ignore_index=True)


def main(path: str, metric: str):
    print(f"reading {path} (metric={metric!r}) ...")
    df = _load_all_sheets_long(path, "value")
    print(f"파싱된 행수: {len(df)}")

    dup = df.duplicated(subset=["ticker", "date"]).sum()
    if dup:
        print(f"중복 (ticker,date) {dup}건 발견 — 첫 값만 남기고 제거")
        df = df.drop_duplicates(subset=["ticker", "date"], keep="first")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    unknown = set(df["ticker"].unique()) - set(instruments_by_ticker)
    if unknown:
        print(f"경고: instruments에 없는 티커 {len(unknown)}건은 건너뜁니다: {sorted(unknown)[:10]}...")
        df = df[~df["ticker"].isin(unknown)]

    rows = [
        dict(instrument_id=instruments_by_ticker[r.ticker], date=r.date.date(), metric=metric, value=float(r.value))
        for r in df.itertuples(index=False)
    ]

    print(f"upserting {len(rows)}행 ...")
    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(MonthlyFundamental).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "metric"], set_={"value": stmt.excluded.value}
        )
        db.execute(stmt)
        db.commit()
        print(f"  {min(i + batch_size, len(rows))}/{len(rows)}")

    print("done.")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("사용법: python scripts/load_monthly_fundamentals.py <경로> <metric명>")
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
