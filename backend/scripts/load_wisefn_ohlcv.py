"""WISEfn/DataGuide류 벌크 OHLCV 엑셀(wide, multi-sheet) 로더.

시트 구성: {거래량,저가,고가,시가,종가}{1,2,3} — 필드별로 종목이 여러 시트에 나뉘어 있음.
각 시트: 0~13행 메타 헤더(3행=Frequency, 7행=Code, 8행=Name), 14행부터 데이터(0열=날짜, 이후 열=종목별 값).
전체 시트의 Frequency(D/M)를 읽어 prices.period로 사용한다. 거래량 시트만 다른 주기(예: 일봉)로
따로 수집된 경우, OHLC 주기에 맞춰 집계해서 합친다.

사용법: python scripts/load_wisefn_ohlcv.py <xlsx_path>
"""
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy.dialects.postgresql import insert as pg_insert

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: F401  # 전체 모델을 등록해 relationship 문자열 참조를 해결
from app.db.session import SessionLocal
from app.models.instrument import Instrument
from app.models.market_holiday import MarketHoliday
from app.models.price import Price

FIELD_SHEET_PREFIXES = {
    "open": "시가",
    "high": "고가",
    "low": "저가",
    "close": "종가",
    "volume": "거래량",
}
DATA_START_ROW = 14
CODE_ROW = 7
NAME_ROW = 8
FREQUENCY_ROW = 3


def _clean_ticker(code: str) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def detect_frequency(path: str, prefix: str) -> str:
    """해당 필드의 첫 시트에서 Frequency(D/M 등)를 읽는다."""
    xl = pd.ExcelFile(path)
    sheet = next(s for s in xl.sheet_names if s.startswith(prefix))
    df = pd.read_excel(path, sheet_name=sheet, header=None, nrows=FREQUENCY_ROW + 1)
    return str(df.iloc[FREQUENCY_ROW, 1]).strip()


def load_field_long(path: str, sheet_prefix: str, column_name: str) -> pd.DataFrame:
    """해당 필드(sheet_prefix로 시작하는 시트들)를 모두 읽어 (ticker, date, column_name) long 형태로 합친다."""
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


def load_ticker_names(path: str) -> dict[str, str]:
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


def main(path: str):
    print(f"reading {path} ...")

    ohlc_freq = detect_frequency(path, FIELD_SHEET_PREFIXES["close"])
    vol_freq = detect_frequency(path, FIELD_SHEET_PREFIXES["volume"])
    period = {"D": "D", "M": "M"}.get(ohlc_freq)
    if period is None:
        raise ValueError(f"지원하지 않는 Frequency: {ohlc_freq!r} (D 또는 M만 지원)")
    print(f"OHLC frequency={ohlc_freq!r}, volume frequency={vol_freq!r} -> period={period!r}")

    ohlc = None
    for field in ("open", "high", "low", "close"):
        long = load_field_long(path, FIELD_SHEET_PREFIXES[field], field)
        ohlc = long if ohlc is None else ohlc.merge(long, on=["ticker", "date"], how="outer")

    ohlc = ohlc.dropna(subset=["close"])
    print(f"OHLC rows (has close): {len(ohlc)}")

    vol = load_field_long(path, FIELD_SHEET_PREFIXES["volume"], "volume")

    if vol_freq == ohlc_freq:
        merged = ohlc.merge(vol, on=["ticker", "date"], how="left")
    else:
        # 거래량 주기가 OHLC보다 세분화된 경우(예: 거래량만 일봉) OHLC 주기에 맞춰 합산한다.
        ohlc["_bucket"] = ohlc["date"].dt.to_period(period)
        vol["_bucket"] = vol["date"].dt.to_period(period)
        vol_agg = vol.groupby(["ticker", "_bucket"], as_index=False)["volume"].sum()
        merged = ohlc.merge(vol_agg, on=["ticker", "_bucket"], how="left").drop(columns="_bucket")

    names = load_ticker_names(path)

    db = SessionLocal()

    if period == "D":
        holidays = {r[0] for r in db.query(MarketHoliday.date).all()}
        before = len(merged)
        merged = merged[~merged["date"].dt.date.isin(holidays)]
        print(f"휴장일 필터: {before - len(merged)}건 제외 (원본이 직전 거래일 값으로 채워 넣은 유령 행)")

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    created = 0
    for ticker in merged["ticker"].unique():
        if ticker in instruments_by_ticker:
            continue
        instrument = Instrument(
            ticker=ticker,
            name=names.get(ticker, ticker),
            asset_type="stock",
        )
        db.add(instrument)
        db.flush()
        instruments_by_ticker[ticker] = instrument.id
        created += 1
    db.commit()
    print(f"instruments auto-created: {created}")

    rows = []
    for r in merged.itertuples(index=False):
        rows.append(
            dict(
                instrument_id=instruments_by_ticker[r.ticker],
                date=r.date.date(),
                period=period,
                open=None if pd.isna(r.open) else float(r.open),
                high=None if pd.isna(r.high) else float(r.high),
                low=None if pd.isna(r.low) else float(r.low),
                close=float(r.close),
                volume=None if pd.isna(r.volume) else int(r.volume),
            )
        )

    print(f"upserting {len(rows)} price rows ...")
    batch_size = 5000
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        stmt = pg_insert(Price).values(batch)
        stmt = stmt.on_conflict_do_update(
            index_elements=["instrument_id", "date", "period"],
            set_={
                "open": stmt.excluded.open,
                "high": stmt.excluded.high,
                "low": stmt.excluded.low,
                "close": stmt.excluded.close,
                "volume": stmt.excluded.volume,
            },
        )
        db.execute(stmt)
        db.commit()
        print(f"  {min(i + batch_size, len(rows))}/{len(rows)}")

    print("done.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/OHLCV.xlsx"
    main(target)
