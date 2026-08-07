"""KOSPI/KOSDAQ "전체" 상장종목 스냅샷을 반기별(6/30, 12/31)로 과거까지 백필한다.

load_index_memberships_pykrx.py의 load_market_snapshot()은 "오늘" 시점 1회만
적재해서, 지금까지 index_memberships에는 KOSPI/KOSDAQ 전체 스냅샷이 최근 것만
있었다(KOSPI200/KOSDAQ150은 반기별로 2018년부터 쌓여있는 것과 대조적). 이 스크립트는
같은 반기 날짜 리스트로 KOSPI/KOSDAQ 전체를 과거까지 채운다 — pykrx의
get_market_ticker_list(date, market=...)가 그 날짜 기준 실제 상장종목 목록을
반환하므로(인덱스 구성종목과 같은 KRX 데이터 소스), 과거 시점도 그대로 조회 가능하다.

사전 준비: backend/.env 에 KRX_ID, KRX_PW 필요(load_index_memberships_pykrx.py와 동일).

사용법: python scripts/backfill_market_memberships.py
"""
import datetime
import sys
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import text

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from scripts.load_index_memberships_pykrx import _half_year_dates, _recent_trading_day, load_market_snapshot  # noqa: E402


def main():
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    today = _recent_trading_day()
    price_start = db.execute(text("SELECT MIN(date) FROM prices")).scalar()
    start_date = price_start or datetime.date(2018, 12, 31)
    dates = _half_year_dates(start_date, today)
    print(f"=== KOSPI/KOSDAQ 전체 반기별 백필: {start_date} ~ {today} ({len(dates)}개 시점) ===")

    for d in dates:
        load_market_snapshot(db, instruments_by_ticker, d)

    print("\ndone.")


if __name__ == "__main__":
    main()
