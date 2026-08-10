"""pykrx로 상장주식수(shares_outstanding_monthly)를 매월 자동 수집해
monthly_fundamentals 테이블에 적재한다. WISEfn 수동 다운로드가 필요한 나머지
팩터(유동주식비율/EBITDA 계열)와 달리, 상장주식수는 KRX 공개데이터라 자동화 가능
(docs/plans/01-data-management.md "월간 데이터 수집 체크리스트" 참고).

사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요
(2025-12-27부터 KRX 정보데이터시스템이 로그인 필수로 전환됨 — data.krx.co.kr 가입 필요.
load_index_memberships_pykrx.py와 동일 계정 재사용).

기준일 결정: 인자 없이 실행하면 "오늘이 이번 달의 마지막 거래일인지"를 스스로 판단해서,
맞으면 오늘 데이터를 수집하고 아니면 조용히 종료한다(cron이 "매월 마지막날"을 직접 지정할
수 없어서 — 매일 월말 근처(예: 25~31일) 장마감 후 돌리면서 스크립트가 알아서 걸러내는
방식). --date로 특정 날짜가 속한 달의 마지막 거래일을 직접 지정할 수 있다(백필용, 오늘이
아니어도 무조건 실행).

사용법:
  python scripts/load_shares_outstanding_pykrx.py                    # 오늘이 월말 거래일일 때만 수집
  python scripts/load_shares_outstanding_pykrx.py --date 2026-06-15  # 2026년 6월 마지막 거래일(백필)
"""
import argparse
import datetime
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy.dialects.postgresql import insert as pg_insert

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402  (KRX_ID/KRX_PW가 os.environ에 있어야 하므로 load_dotenv 이후 import)

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.models.monthly_fundamental import MonthlyFundamental  # noqa: E402

METRIC = "shares_outstanding_monthly"
MAX_LOOKBACK_DAYS = 10  # 월말이 주말/휴일이면 최대 며칠 전까지 되짚어볼지


def _shift_month(year: int, month: int, offset: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) + offset
    return total // 12, total % 12 + 1


def _last_calendar_day(year: int, month: int) -> datetime.date:
    next_y, next_m = _shift_month(year, month, 1)
    return datetime.date(next_y, next_m, 1) - datetime.timedelta(days=1)


def last_trading_day_of_month(year: int, month: int) -> datetime.date:
    """그 달의 마지막 거래일을 pykrx 실제 데이터 존재 여부로 역산한다(휴장일 캘린더에
    의존하지 않고 KRX 응답 자체를 기준으로 삼아 더 안정적)."""
    day = _last_calendar_day(year, month)
    for _ in range(MAX_LOOKBACK_DAYS):
        tickers = stock.get_market_ticker_list(day.strftime("%Y%m%d"), market="KOSPI")
        if tickers:
            return day
        day -= datetime.timedelta(days=1)
    raise RuntimeError(f"{year}-{month:02d} 마지막 거래일을 찾지 못했습니다 (KRX_ID/KRX_PW 설정을 확인하세요).")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--date", type=datetime.date.fromisoformat, default=None,
        help="이 날짜가 속한 달의 마지막 거래일을 기준일로 강제 지정(백필용, 오늘 여부와 무관하게 실행)",
    )
    return p.parse_args()


def main():
    args = parse_args()
    if args.date is not None:
        target_year, target_month = args.date.year, args.date.month
        as_of = last_trading_day_of_month(target_year, target_month)
    else:
        today = datetime.date.today()
        as_of = last_trading_day_of_month(today.year, today.month)
        if as_of != today:
            print(f"오늘({today})은 {today.year}-{today.month:02d}의 마지막 거래일이 아닙니다"
                  f"(마지막 거래일: {as_of}). 아무 작업 없이 종료.")
            return

    print(f"기준일: {as_of}")

    df = stock.get_market_cap_by_ticker(as_of.strftime("%Y%m%d"), market="ALL")
    if "상장주식수" not in df.columns:
        raise RuntimeError(f"pykrx 응답에 상장주식수 컬럼이 없습니다: {df.columns.tolist()}")
    print(f"pykrx 조회: {len(df)}종목")

    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}
    unknown = set(df.index) - set(instruments_by_ticker)
    if unknown:
        print(f"경고: instruments에 없는 티커 {len(unknown)}건은 건너뜁니다 (예: {sorted(unknown)[:10]})")

    rows = [
        dict(instrument_id=instruments_by_ticker[ticker], date=as_of, metric=METRIC, value=float(row["상장주식수"]))
        for ticker, row in df.iterrows()
        if ticker in instruments_by_ticker
    ]
    print(f"upserting {len(rows)}행 (metric={METRIC!r}) ...")

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
    db.close()


if __name__ == "__main__":
    main()
