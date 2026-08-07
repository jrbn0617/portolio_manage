"""pykrx로 KOSPI 지수(코스피/코스닥 공통 거래소 캘린더) 일별 시세를 조회해
실제 거래일 목록을 얻고, 그 기간의 평일(월~금) 중 거래일이 아닌 날을 휴장일로
market_holidays 테이블에 적재한다.

지수 하나만 조회하는 가벼운 호출(수천 건)이라 종목별 대량조회와 달리 KRX 차단
우려가 적다.

사전 준비: backend/.env 에 KRX_ID, KRX_PW 설정 필요.

사용법: python scripts/load_market_holidays.py [start_yyyymmdd] [end_yyyymmdd]
"""
import sys
from datetime import date, timedelta
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))
load_dotenv(BACKEND_DIR / ".env")

from pykrx import stock  # noqa: E402

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.market_holiday import MarketHoliday  # noqa: E402

KOSPI_INDEX_CODE = "1001"  # 코스피 종합지수


def _daterange_weekdays(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:  # 월~금
            yield d
        d += timedelta(days=1)


def main(start_str: str, end_str: str):
    start = date(int(start_str[:4]), int(start_str[4:6]), int(start_str[6:8]))
    end = date(int(end_str[:4]), int(end_str[4:6]), int(end_str[6:8]))
    print(f"조회 구간: {start} ~ {end}")

    df = stock.get_index_ohlcv_by_date(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"), KOSPI_INDEX_CODE)
    trading_days = {ts.date() for ts in df.index}
    print(f"실제 거래일 {len(trading_days)}건 조회됨")

    weekdays = set(_daterange_weekdays(start, end))
    holidays = sorted(weekdays - trading_days)
    print(f"평일 중 휴장일 {len(holidays)}건 발견")

    db = SessionLocal()
    existing = {r[0] for r in db.query(MarketHoliday.date).all()}
    new_holidays = [h for h in holidays if h not in existing]
    for h in new_holidays:
        db.add(MarketHoliday(date=h))
    db.commit()
    print(f"신규 적재 {len(new_holidays)}건 (기존 {len(existing)}건과 중복 제외)")


if __name__ == "__main__":
    start_arg = sys.argv[1] if len(sys.argv) > 1 else "20181228"
    end_arg = sys.argv[2] if len(sys.argv) > 2 else date.today().strftime("%Y%m%d")
    main(start_arg, end_arg)
