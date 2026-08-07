"""WISEfn류 벌크 엑셀(wide, multi-sheet)에서 "실제(비조정) 종가"만 뽑아 raw_closes에 적재한다.
load_wisefn_ohlcv.py와 같은 시트 레이아웃(0~13행 메타 헤더, 14행부터 데이터)을 가정한다.

사용법: python scripts/load_raw_closes.py <xlsx_path>
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.raw_close import RawClose  # noqa: E402

DATA_START_ROW = 14
CODE_ROW = 7


def _clean_ticker(code: str) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def load_all_sheets_long(path: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    frames = []
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None)
        codes = [_clean_ticker(c) for c in raw.iloc[CODE_ROW, 1:]]
        block = raw.iloc[DATA_START_ROW:, :].copy()
        block.columns = ["date"] + codes
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date"])
        long = block.melt(id_vars="date", var_name="ticker", value_name="close")
        long = long.dropna(subset=["close"])
        frames.append(long)
        print(f"  {sheet}: {len(long)}건")
    return pd.concat(frames, ignore_index=True)


def main(path: str):
    print(f"reading {path} ...")
    long = load_all_sheets_long(path)
    print(f"총 {len(long)}건")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    unmatched = set(long["ticker"].unique()) - set(instruments_by_ticker.keys())
    if unmatched:
        print(f"종목마스터에 없어 건너뛰는 티커 {len(unmatched)}개 (자동 등록하지 않음 — 실제종가는 보조 데이터)")

    rows = []
    for r in long.itertuples(index=False):
        instrument_id = instruments_by_ticker.get(r.ticker)
        if instrument_id is None:
            continue
        rows.append(dict(instrument_id=instrument_id, date=r.date.date(), close=float(r.close)))

    print(f"적재 대상 {len(rows)}건")
    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(RawClose).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date"],
            set_={"close": stmt.excluded.close},
        )
        db.execute(stmt)
        db.commit()
        print(f"  {min(i + batch_size, len(rows))}/{len(rows)}")

    print("done.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/kosdaq종가.xlsx"
    main(target)
