"""벤치마크 지수(코스피/코스닥/코스피200/코스닥150) 종가 시계열을
블룸버그 엑셀에서 적재한다. 백테스트 결과와 비교할 벤치마크용.

파일 형식: 단일 시트, Date 컬럼 + 지수 4종. 컬럼명은 "<지수> Index  (L3)"처럼
블룸버그 차트 축 표시가 붙어 나오는데, 이 접미사는 내보낼 때마다 달라지므로
(bm_kospikosdaq.xlsx는 L3/L1/L4/R3, kk.xlsx는 R1/L1/L2/R2) "Index" 앞의
지수 토큰만 보고 매칭한다.

**모두 TR(총수익) 지수 기준이다.** 블룸버그 차트 내보내기는 절대 레벨이 차트
시작일 기준으로 정규화돼 있어 파일마다 레벨이 다르게 나온다(같은 구간에서
kk.xlsx가 bm_kospikosdaq.xlsx보다 코스피 +2.9%, 코스피200 +3.2% 높음). 일간
수익률은 상관 0.9999+로 동일하다. 쓰는 쪽이 전부 스케일 무관이라(레짐필터는
종가와 자기 이동평균 비교, 별첨2 BM은 시작일 100 리베이스) 레벨 차이는 무해하지만,
**한 파일로 전 구간을 덮어써서 시계열 중간에 레벨 단차가 생기지 않게 한다.**

instruments에 asset_type='index'로 4개 종목을 자동 등록(없으면)하고, prices(period='D',
close만 채움)에 적재한다. 지수행은 close 외 OHLC/거래량을 채우지 않는다.

--holidays: 지수 캘린더가 곧 거래소 캘린더이므로, 파일 구간의 평일 중 시세가 없는
날을 market_holidays에 등록한다(load_market_holidays.py와 같은 판정을 KRX 호출 없이
파일만으로 수행 — 과거 구간은 pykrx로 백필하지 않는다는 프로젝트 규칙에 맞춘다).

사용법: python scripts/load_benchmark_indices.py [reference/kk.xlsx] [--holidays]
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

# 블룸버그 티커 토큰 -> (우리 ticker, 이름)
INDEX_TOKENS = {
    "KOSPI2": ("KOSPI200", "코스피200"),
    "KOSDAQ": ("KOSDAQ", "코스닥종합"),
    "KOSPI": ("KOSPI", "코스피종합"),
    "KOSDQ150": ("KOSDAQ150", "코스닥150"),
    "KOSDAQ150": ("KOSDAQ150", "코스닥150"),  # 철자 변형 대비
}


def resolve_columns(columns) -> dict[str, tuple[str, str]]:
    """실제 컬럼명 -> (ticker, name). "KOSPI2 Index  (R2)" 에서 "KOSPI2"만 보고 매칭."""
    resolved: dict[str, tuple[str, str]] = {}
    for col in columns:
        token = str(col).split("Index")[0].strip()
        if token in INDEX_TOKENS:
            resolved[col] = INDEX_TOKENS[token]
    found = {t for t, _ in resolved.values()}
    missing = {t for t, _ in INDEX_TOKENS.values()} - found
    if missing:
        raise ValueError(f"지수 컬럼을 찾지 못함: {sorted(missing)} (실제 컬럼: {list(columns)})")
    return resolved


def register_holidays(db, trading_days: set) -> None:
    """파일 구간의 평일 중 지수 시세가 없는 날 = 휴장일."""
    from datetime import timedelta

    start, end = min(trading_days), max(trading_days)
    weekdays, d = set(), start
    while d <= end:
        if d.weekday() < 5:
            weekdays.add(d)
        d += timedelta(days=1)

    existing = {r[0] for r in db.query(MarketHoliday.date).all()}
    new = sorted(weekdays - trading_days - existing)
    conflicts = sorted(existing & trading_days)
    if conflicts:
        print(f"  경고: 휴장일로 등록됐는데 시세가 있는 날 {len(conflicts)}건 — {conflicts[:5]}")
    for h in new:
        db.add(MarketHoliday(date=h))
    db.commit()
    print(f"  휴장일 신규 등록 {len(new)}건" + (f": {new}" if new else ""))


def main(path: str, do_holidays: bool):
    print(f"reading {path} ...")
    raw = pd.read_excel(path, sheet_name=0, header=0)
    raw["Date"] = pd.to_datetime(raw["Date"]).dt.date
    raw = raw.sort_values("Date").reset_index(drop=True)

    column_map = resolve_columns(raw.columns)
    dup = raw["Date"].duplicated().sum()
    if dup:
        raise ValueError(f"중복 날짜 {dup}건")

    db = SessionLocal()

    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    for _, (ticker, name) in column_map.items():
        if ticker not in instruments_by_ticker:
            inst = Instrument(ticker=ticker, name=name, asset_type="index")
            db.add(inst)
            db.flush()
            instruments_by_ticker[ticker] = inst.id
            print(f"instrument 신규 등록: {ticker} {name}")
    db.commit()

    total = 0
    for col, (ticker, name) in column_map.items():
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

    print(f"\n지수 적재 완료 — 총 {total}건")

    if do_holidays:
        print("휴장일 판정 중 (지수 캘린더 = 거래소 캘린더) ...")
        register_holidays(db, set(raw["Date"]))


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    target = args[0] if args else "../reference/kk.xlsx"
    main(target, do_holidays="--holidays" in sys.argv)
