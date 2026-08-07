"""reference/가격빈것.xlsx 전용 로더.

유동주식시가총액 지수 재현 시 raw_close/OHLC가 없어 제외됐던 종목들을 보강하기 위한
파일. WISEfn 단일 시트, 종목당 5개 컬럼(수정고가/수정시가/수정저가/수정주가/종가)이
가로로 이어붙은 포맷 — 지금까지 쓰던 "필드별 시트" 포맷과 다르다.

수정고가/수정시가/수정저가/수정주가 -> Price.high/open/low/close (분할조정 계열)
종가 -> Price.raw_close (실제 체결가)

사용법: python scripts/load_price_gaps.py [../reference/가격빈것.xlsx]
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.market_holiday import MarketHoliday
from app.models.price import Price

FIELD_MAP = {"수정고가": "high", "수정시가": "open", "수정저가": "low", "수정주가": "close", "종가": "raw_close"}
CODE_ROW = 8
LABEL_ROW = 14
DATA_START_ROW = 15


def main(path: str):
    print(f"reading {path} ...")
    raw = pd.read_excel(path, sheet_name=0, header=None)

    codes = raw.iloc[CODE_ROW - 1]
    labels = raw.iloc[LABEL_ROW - 1]
    dates = pd.to_datetime(raw.iloc[DATA_START_ROW - 1 :, 0]).dt.date.reset_index(drop=True)

    blocks: dict[str, dict[str, int]] = {}
    for col in range(1, raw.shape[1]):
        code = codes[col]
        label = labels[col]
        if pd.isna(code) or pd.isna(label) or label not in FIELD_MAP:
            continue
        ticker = str(code).lstrip("A")
        blocks.setdefault(ticker, {})[FIELD_MAP[label]] = col

    print(f"tickers found: {len(blocks)}")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}

    missing_instruments = [t for t in blocks if t not in instruments_by_ticker]
    if missing_instruments:
        print(f"경고: DB에 없는 티커 {len(missing_instruments)}개 — 건너뜀: {missing_instruments}")

    all_rows: list[dict] = []
    for ticker, cols in blocks.items():
        instrument_id = instruments_by_ticker.get(ticker)
        if instrument_id is None:
            continue
        data = raw.iloc[DATA_START_ROW - 1 :, list(cols.values())]
        data.columns = list(cols.keys())
        data = data.reset_index(drop=True)
        data["date"] = dates
        data = data.dropna(subset=["close"]) if "close" in data.columns else data.dropna(how="all", subset=list(cols.keys()))
        data = data[~data["date"].apply(lambda d: d.weekday() >= 5 or d in holidays)]
        for _, r in data.iterrows():
            row = {"instrument_id": instrument_id, "date": r["date"], "period": "D"}
            for field in ("open", "high", "low", "close", "raw_close"):
                if field in cols and pd.notna(r.get(field)):
                    row[field] = float(r[field])
            all_rows.append(row)

    df = pd.DataFrame(all_rows).drop_duplicates(subset=["instrument_id", "date"], keep="first")
    print(f"총 {len(df)}행 적재 예정 ({df['date'].min()} ~ {df['date'].max()})")

    batch_size = 5000
    records = df.to_dict("records")
    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        stmt = pg_insert(Price).values(batch)
        set_cols = {c: getattr(stmt.excluded, c) for c in ("open", "high", "low", "close", "raw_close") if c in df.columns}
        stmt = stmt.on_conflict_do_update(index_elements=["instrument_id", "date", "period"], set_=set_cols)
        db.execute(stmt)
        db.commit()

    print("완료")
    db.close()


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/가격빈것.xlsx"
    main(target)
