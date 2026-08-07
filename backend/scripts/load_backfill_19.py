"""코스닥150 2019-12-31 스냅샷 편입종목 중 가격 이력이 없던 19종목
(코스피 이전상장/상장폐지/합병 등으로 기존 WISEfn 코스닥 전용 대량적재에서 누락됐던 종목)의
과거 가격을 사용자가 준비한 reference/백필.xlsx로 채운다.

파일 형식: 단일 시트, 종목당 5개 컬럼 블록(수정주가/수정시가/수정고가/수정저가/거래량).
7행=Code(A접두), 8행=Name, 13행=필드 라벨, 14행부터 날짜별 데이터.
"수정주가"류는 기존 prices.close와 동일하게 현재 시점 기준 분할조정 종가로 취급한다
(load_wisefn_ohlcv.py와 동일 관례 — 2026-08-04/05 구간 값이 daily_update.py가 이미
수집해둔 실제 종가와 정확히 일치함을 확인함).

사용법: python scripts/load_backfill_19.py [reference/백필.xlsx]
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
from app.services.derived_prices import recompute_dividend_adjusted, recompute_monthly_bar

CODE_ROW = 7
NAME_ROW = 8
LABEL_ROW = 13
DATA_START_ROW = 14
FIELD_ORDER = ["close", "open", "high", "low", "volume"]  # 수정주가,수정시가,수정고가,수정저가,거래량 순서


def _clean_ticker(code: str) -> str:
    code = str(code).strip()
    return code[1:] if code.startswith("A") else code


def _clean_name(name: str) -> str:
    return str(name).replace("(주)", "").strip()


def parse(path: str) -> tuple[pd.DataFrame, dict[str, str]]:
    raw = pd.read_excel(path, sheet_name=0, header=None)
    codes = raw.iloc[CODE_ROW, 1:].tolist()
    names = raw.iloc[NAME_ROW, 1:].tolist()

    names_by_ticker: dict[str, str] = {}
    frames = []
    for i in range(0, len(codes), 5):
        ticker = _clean_ticker(codes[i])
        names_by_ticker[ticker] = _clean_name(names[i])
        block = raw.iloc[DATA_START_ROW:, [0] + list(range(1 + i, 6 + i))].copy()
        block.columns = ["date"] + FIELD_ORDER
        block["date"] = pd.to_datetime(block["date"], errors="coerce")
        block = block.dropna(subset=["date", "close"])
        block["ticker"] = ticker
        frames.append(block)

    df = pd.concat(frames, ignore_index=True)
    return df, names_by_ticker


def main(path: str):
    print(f"reading {path} ...")
    df, names_by_ticker = parse(path)
    print(f"파싱된 티커 {len(names_by_ticker)}개, 행 {len(df)}건")

    db = SessionLocal()

    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}
    before = len(df)
    df = df[~df["date"].dt.date.isin(holidays)]
    print(f"휴장일 필터: {before - len(df)}건 제외")

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    missing = [t for t in names_by_ticker if t not in instruments_by_ticker]
    if missing:
        raise RuntimeError(f"instruments 테이블에 없는 티커 발견(예상 밖): {missing}")

    # placeholder(name == ticker)였던 종목은 파일의 실제 종목명으로 보정
    updated_names = 0
    for ticker, name in names_by_ticker.items():
        inst = db.query(Instrument).filter(Instrument.id == instruments_by_ticker[ticker]).first()
        if inst.name == ticker and name:
            inst.name = name
            updated_names += 1
    db.commit()
    print(f"placeholder 종목명 보정: {updated_names}건")

    rows = []
    for r in df.itertuples(index=False):
        rows.append(
            dict(
                instrument_id=instruments_by_ticker[r.ticker],
                date=r.date.date(),
                period="D",
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

    print("월봉/배당조정지수 재계산 중 ...")
    for ticker in names_by_ticker:
        instrument_id = instruments_by_ticker[ticker]
        months = sorted({(d.year, d.month) for d in df[df["ticker"] == ticker]["date"].dt.date})
        for year, month in months:
            recompute_monthly_bar(db, instrument_id, year, month)
        db.commit()
        recompute_dividend_adjusted(db, instrument_id, force_full=True)
        db.commit()
        print(f"  {ticker} {names_by_ticker[ticker]}: {len(months)}개월 재계산")

    print("done.")


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "../reference/백필.xlsx"
    main(target)
