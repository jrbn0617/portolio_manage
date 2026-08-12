"""pykrx로 KOSPI/KOSDAQ 시장구분(현재 스냅샷 1회) + KOSPI200/KOSDAQ150 구성종목
(반기별: 6/30, 12/31) 을 index_memberships 테이블에 적재한다.

사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요
(2025-12-27부터 KRX 정보데이터시스템이 로그인 필수로 전환됨 — data.krx.co.kr 가입 필요).

사용법: python scripts/load_index_memberships_pykrx.py
"""
import datetime
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402  (KRX_ID/KRX_PW가 os.environ에 있어야 하므로 load_dotenv 이후 import)

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.instrument_rules import filter_common  # noqa: E402
from app.services.upload_service import _upsert_index_membership  # noqa: E402

REQUEST_DELAY_SEC = 1
MAX_LOOKBACK_DAYS = 7  # 최근 거래일을 찾기 위해 최대 며칠 전까지 되짚어볼지


def _recent_trading_day() -> datetime.date:
    """오늘 기준으로 데이터가 실제로 존재하는 가장 최근 날짜를 찾는다 (주말/휴일 대응)."""
    day = datetime.date.today()
    for _ in range(MAX_LOOKBACK_DAYS):
        tickers = stock.get_market_ticker_list(day.strftime("%Y%m%d"), market="KOSPI")
        if tickers:
            return day
        day -= datetime.timedelta(days=1)
    raise RuntimeError("최근 거래일을 찾지 못했습니다 (KRX_ID/KRX_PW 설정을 확인하세요).")


def _half_year_dates(start: datetime.date, end: datetime.date) -> list[datetime.date]:
    dates = []
    for year in range(start.year, end.year + 1):
        for month, day in ((6, 30), (12, 31)):
            d = datetime.date(year, month, day)
            if start <= d <= end:
                dates.append(d)
    return dates


def _find_index_ticker(market: str, name: str) -> str:
    for code in stock.get_index_ticker_list(market=market):
        if stock.get_index_ticker_name(code) == name:
            return code
    raise ValueError(f"{market} 시장에서 지수 '{name}'을(를) 찾지 못했습니다.")


def load_market_snapshot(db, instruments_by_ticker: dict[str, int], as_of: datetime.date):
    for market in ("KOSPI", "KOSDAQ"):
        tickers = filter_common(stock.get_market_ticker_list(as_of.strftime("%Y%m%d"), market=market))
        print(f"{market} {as_of}: {len(tickers)}개 종목")
        for ticker in tickers:
            row = {"ticker": ticker, "index_name": market, "as_of_date": as_of}
            with db.begin_nested():
                _upsert_index_membership(db, row, instruments_by_ticker)
        db.commit()
        time.sleep(REQUEST_DELAY_SEC)


def load_index_history(db, instruments_by_ticker: dict[str, int], index_name: str, index_ticker: str, dates: list[datetime.date]):
    for d in dates:
        members = filter_common(stock.get_index_portfolio_deposit_file(index_ticker, d.strftime("%Y%m%d"), alternative=True))
        print(f"{index_name} {d}: {len(members)}개 종목")
        for ticker in members:
            row = {"ticker": ticker, "index_name": index_name, "as_of_date": d}
            with db.begin_nested():
                _upsert_index_membership(db, row, instruments_by_ticker)
        db.commit()
        time.sleep(REQUEST_DELAY_SEC)


def main():
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    today = _recent_trading_day()
    print(f"기준 거래일: {today}")

    print("\n=== KOSPI/KOSDAQ 시장구분 (현재 스냅샷) ===")
    load_market_snapshot(db, instruments_by_ticker, today)

    price_start = db.execute(text("SELECT MIN(date) FROM prices")).scalar()
    start_date = price_start or datetime.date(2018, 12, 31)
    dates = _half_year_dates(start_date, today)
    print(f"\n=== KOSPI200/KOSDAQ150 반기별 백필: {start_date} ~ {today} ({len(dates)}개 시점) ===")

    kospi200_code = _find_index_ticker("KOSPI", "코스피 200")
    kosdaq150_code = _find_index_ticker("KOSDAQ", "코스닥 150")
    print(f"KOSPI200 지수코드={kospi200_code}, KOSDAQ150 지수코드={kosdaq150_code}")

    load_index_history(db, instruments_by_ticker, "KOSPI200", kospi200_code, dates)
    load_index_history(db, instruments_by_ticker, "KOSDAQ150", kosdaq150_code, dates)

    print("\ndone.")


if __name__ == "__main__":
    main()
