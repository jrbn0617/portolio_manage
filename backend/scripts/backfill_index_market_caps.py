"""index_memberships에 이미 적재된 KOSPI200 반기 스냅샷 시점들에 대해
시가총액(index_market_caps)을 1회성으로 채운다.

스냅샷 시점이 비거래일(예: 12/31이 휴장일)이면 그 이하 마지막 실제 거래일 기준으로
KRX 시가총액을 조회한다 — app.services.index_market_cap_service 참고.

사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요.

사용법: python scripts/backfill_index_market_caps.py
"""
import sys
import time
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import distinct

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.index_membership import IndexMembership  # noqa: E402
from app.models.instrument import Instrument  # noqa: E402
from app.services.index_market_cap_service import fetch_and_store_index_market_caps  # noqa: E402

REQUEST_DELAY_SEC = 1
INDEX_NAME = "KOSPI200"
MARKET = "KOSPI"


def main():
    db = SessionLocal()
    instruments_by_ticker = {t: i for t, i in db.query(Instrument.ticker, Instrument.id).all()}

    dates = [
        d
        for (d,) in db.query(distinct(IndexMembership.as_of_date))
        .filter(IndexMembership.index_name == INDEX_NAME)
        .order_by(IndexMembership.as_of_date)
        .all()
    ]
    print(f"{INDEX_NAME} 스냅샷 시점 {len(dates)}개: {dates}")

    total = 0
    for d in dates:
        count = fetch_and_store_index_market_caps(db, stock, INDEX_NAME, d, MARKET, instruments_by_ticker)
        print(f"  {d}: {count}건 적재")
        total += count
        time.sleep(REQUEST_DELAY_SEC)

    print(f"\n완료 — 총 {total}건 적재")


if __name__ == "__main__":
    main()
