"""벤치마크 지수(코스피/코스닥/코스피200/코스닥150) 종가 시계열을
reference/bm_kospikosdaq.xlsx에서 적재한다. 백테스트 결과와 비교할 벤치마크용.

파일 형식: 단일 시트, 컬럼 = Date, KOSPI2 Index (L3)=코스피200,
KOSDAQ Index (L1)=코스닥종합, KOSPI Index (L4)=코스피종합, KOSDQ150 Index (R3)=코스닥150.

instruments에 asset_type='index'로 4개 종목을 자동 등록(없으면)하고, prices(period='D',
close만 채움)에 적재한다.

사용법: python scripts/load_benchmark_indices.py [reference/bm_kospikosdaq.xlsx]
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.price import Price

COLUMN_MAP = {
    "KOSPI2 Index  (L3)": ("KOSPI200", "코스피200"),
    "KOSDAQ Index  (L1)": ("KOSDAQ", "코스닥종합"),
    "KOSPI Index  (L4)": ("KOSPI", "코스피종합"),
    "KOSDQ150 Index  (R3)": ("KOSDAQ150", "코스닥150"),
}


def main(path: str):
    print(f"reading {path} ...")
    raw = pd.read_excel(path, sheet_name=0, header=0)
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.date

    missing_cols = [c for c in COLUMN_MAP if c not in raw.columns]
    if missing_cols:
        raise ValueError(f"예상 컬럼 없음: {missing_cols} (실제: {list(raw.columns)})")

    db = SessionLocal()

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    for _, (ticker, name) in COLUMN_MAP.items():
        if ticker not in instruments_by_ticker:
            inst = Instrument(ticker=ticker, name=name, asset_type="index")
            db.add(inst)
            db.flush()
            instruments_by_ticker[ticker] = inst.id
            print(f"instrument 신규 등록: {ticker} {name}")
    db.commit()

    total = 0
    for col, (ticker, name) in COLUMN_MAP.items():
        instrument_id = instruments_by_ticker[ticker]
        rows = [
            dict(instrument_id=instrument_id, date=d, period="D", close=float(v))
            for d, v in zip(raw["Date"], raw[col])
        ]
        batch_size = 5000
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(Price).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date", "period"], set_={"close": stmt.excluded.close}
            )
            db.execute(stmt)
        db.commit()
        total += len(rows)
        print(f"  {ticker} {name}: {len(rows)}건 적재 ({raw['Date'].min()} ~ {raw['Date'].max()})")

    print(f"\n완료 — 총 {total}건 적재")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/bm_kospikosdaq.xlsx"
    main(target)
