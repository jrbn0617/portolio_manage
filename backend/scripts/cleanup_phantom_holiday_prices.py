"""prices(period='D')에서 market_holidays에 해당하는 날짜(실제로는 휴장일인데
원본 WISEfn 데이터가 직전 거래일 값으로 채워 넣은 유령 행)를 삭제한다.

사용법: python scripts/cleanup_phantom_holiday_prices.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.db.base import Base  # noqa: E402,F401
from app.db.session import SessionLocal  # noqa: E402
from app.models.market_holiday import MarketHoliday  # noqa: E402
from app.models.price import Price  # noqa: E402


def main():
    db = SessionLocal()
    holidays = [r[0] for r in db.query(MarketHoliday.date).all()]
    print(f"휴장일 {len(holidays)}건 기준으로 정리")

    deleted = (
        db.query(Price)
        .filter(Price.period == "D", Price.date.in_(holidays))
        .delete(synchronize_session=False)
    )
    db.commit()
    print(f"prices(D) 유령 행 {deleted}건 삭제 완료")


if __name__ == "__main__":
    main()
