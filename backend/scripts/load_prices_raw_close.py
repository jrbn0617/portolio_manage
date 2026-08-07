"""WISEfn 벌크 엑셀에서 수정OHLC(prices.open/high/low/close)와 미조정 종가
(prices.raw_close)를 적재한다.

거래량 시트가 없는 파일 대상(코스피 가격.xlsx, kosdaq종가.xlsx)이라
load_wisefn_ohlcv.py(거래량 필수)와는 별도로 분리했다. raw_close는 유동주식
시가총액처럼 "그 시점 실제 체결가"가 필요한 계산에 쓴다(close는 분할 등으로
소급조정된 값이라 그 용도엔 못 씀).

사용법:
  # 코스피: 수정OHLC + 미조정종가를 한 파일에서 동시 적재(신규 종목/신규 거래일 포함)
  python scripts/load_prices_raw_close.py --adjusted "../reference/코스피 가격.xlsx" \
      --raw "../reference/코스피 가격.xlsx" --raw-sheet-prefix 종가

  # 코스닥: 미조정종가만 적재(수정OHLC는 이미 적재돼 있음, 기존 행만 업데이트)
  python scripts/load_prices_raw_close.py --raw "../reference/kosdaq종가.xlsx" --raw-sheet-prefix ""
"""
import argparse
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import bindparam, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.market_holiday import MarketHoliday
from app.models.price import Price

CODE_ROW = 7
NAME_ROW = 8
DATA_START_ROW = 14

ADJUSTED_PREFIXES = {"open": "수정시가", "high": "수정고가", "low": "수정저가", "close": "수정종가"}


def _clean_ticker(code) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _load_field_long(path: str, sheet_prefix: str, column_name: str) -> pd.DataFrame:
    xl = pd.ExcelFile(path)
    sheets = [s for s in xl.sheet_names if s.startswith(sheet_prefix)]
    frames = []
    for sheet in sheets:
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


def _load_ticker_names(path: str) -> dict[str, str]:
    xl = pd.ExcelFile(path)
    names: dict[str, str] = {}
    for sheet in xl.sheet_names:
        raw = pd.read_excel(path, sheet_name=sheet, header=None, nrows=NAME_ROW + 1)
        codes = raw.iloc[CODE_ROW, 1:]
        vals = raw.iloc[NAME_ROW, 1:]
        for code, name in zip(codes, vals):
            ticker = _clean_ticker(code)
            if ticker not in names and pd.notna(name):
                names[ticker] = str(name).strip()
    return names


def main(adjusted_path: str | None, raw_path: str | None, raw_sheet_prefix: str):
    db = SessionLocal()
    names: dict[str, str] = {}
    merged = None

    if adjusted_path:
        print(f"수정OHLC 읽는 중: {adjusted_path}")
        ohlc = None
        for field, prefix in ADJUSTED_PREFIXES.items():
            long = _load_field_long(adjusted_path, prefix, field)
            ohlc = long if ohlc is None else ohlc.merge(long, on=["ticker", "date"], how="outer")
        ohlc = ohlc.dropna(subset=["close"])
        print(f"  수정OHLC 행수: {len(ohlc)}")
        merged = ohlc
        names.update(_load_ticker_names(adjusted_path))

    if raw_path:
        print(f"미조정종가 읽는 중: {raw_path} (sheet prefix={raw_sheet_prefix!r})")
        raw = _load_field_long(raw_path, raw_sheet_prefix, "raw_close")
        print(f"  미조정종가 행수: {len(raw)}")
        if merged is None:
            merged = raw
        else:
            before = len(merged)
            merged = merged.merge(raw, on=["ticker", "date"], how="inner")
            print(f"  수정OHLC·미조정종가 inner join: {before} -> {len(merged)}행")
        names.update(_load_ticker_names(raw_path))

    if merged is None:
        print("--adjusted / --raw 중 하나는 지정해야 합니다.")
        return

    dup_count = merged.duplicated(subset=["ticker", "date"]).sum()
    if dup_count:
        # WISEfn 시트 분할 과정에서 일부 종목이 두 시트에 중복 포함되는 경우가 있음(값은 동일) — 방어적으로 정리
        print(f"중복 (ticker,date) {dup_count}건 발견 — 첫 값만 남기고 제거")
        merged = merged.drop_duplicates(subset=["ticker", "date"], keep="first")

    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}
    before = len(merged)
    merged = merged[~merged["date"].dt.date.isin(holidays)]
    print(f"휴장일 필터: {before - len(merged)}건 제외")

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    has_ohlc = "close" in merged.columns

    if has_ohlc:
        created = 0
        for ticker in merged["ticker"].unique():
            if ticker in instruments_by_ticker:
                continue
            inst = Instrument(ticker=ticker, name=names.get(ticker, ticker), asset_type="stock")
            db.add(inst)
            db.flush()
            instruments_by_ticker[ticker] = inst.id
            created += 1
        db.commit()
        print(f"instruments 신규 등록: {created}건")
    else:
        unknown = set(merged["ticker"].unique()) - set(instruments_by_ticker)
        if unknown:
            print(f"경고: instruments에 없는 티커 {len(unknown)}건은 건너뜁니다: {sorted(unknown)[:10]}...")
            merged = merged[~merged["ticker"].isin(unknown)]

    rows = []
    if has_ohlc:
        for r in merged.itertuples(index=False):
            rows.append(
                dict(
                    instrument_id=instruments_by_ticker[r.ticker],
                    date=r.date.date(),
                    period="D",
                    open=None if pd.isna(r.open) else float(r.open),
                    high=None if pd.isna(r.high) else float(r.high),
                    low=None if pd.isna(r.low) else float(r.low),
                    close=float(r.close),
                    raw_close=None if pd.isna(getattr(r, "raw_close", None)) else float(r.raw_close),
                )
            )
        set_cols = ["open", "high", "low", "close", "raw_close"]

        print(f"upserting {len(rows)} 행 (수정OHLC+raw_close) ...")
        batch_size = 5000
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            stmt = pg_insert(Price).values(batch)
            stmt = stmt.on_conflict_do_update(
                index_elements=["instrument_id", "date", "period"],
                set_={c: getattr(stmt.excluded, c) for c in set_cols},
            )
            db.execute(stmt)
            db.commit()
            print(f"  {min(i + batch_size, len(rows))}/{len(rows)}")
    else:
        # raw_close만 있는 경우(예: kosdaq종가.xlsx) — 기존 행이 있을 때만 그 값만 갱신한다.
        # INSERT..ON CONFLICT 대신 순수 UPDATE를 써서, 대응하는 행이 없는 날짜는
        # (close가 NOT NULL이라 새로 만들 수 없으므로) 조용히 건너뛴다.
        params = [
            dict(iid=instruments_by_ticker[r.ticker], d=r.date.date(), rc=float(r.raw_close))
            for r in merged.itertuples(index=False)
        ]
        # Price(ORM 클래스) 대신 __table__(Core)을 대상으로 update해야 ORM의
        # "bulk update by primary key" 강제 규칙(각 파라미터에 PK 필요)을 피할 수 있다.
        price_table = Price.__table__
        stmt = (
            update(price_table)
            .where(
                price_table.c.instrument_id == bindparam("iid"),
                price_table.c.date == bindparam("d"),
                price_table.c.period == "D",
            )
            .values(raw_close=bindparam("rc"))
        )
        print(f"updating raw_close for {len(params)}행 후보 ...")
        batch_size = 5000
        updated_rowcount = 0
        for i in range(0, len(params), batch_size):
            batch = params[i : i + batch_size]
            result = db.execute(stmt, batch)
            updated_rowcount += result.rowcount
            db.commit()
            print(f"  {min(i + batch_size, len(params))}/{len(params)}")
        print(f"실제 매칭되어 갱신된 행: {updated_rowcount} / 후보 {len(params)}건")

    print("done.")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--adjusted", default=None, help="수정OHLC 소스 파일 경로")
    p.add_argument("--raw", default=None, help="미조정종가 소스 파일 경로")
    p.add_argument("--raw-sheet-prefix", default="종가", help="raw 파일에서 미조정종가 시트를 고를 접두어 (전체시트면 빈 문자열)")
    args = p.parse_args()
    main(args.adjusted, args.raw, args.raw_sheet_prefix)
