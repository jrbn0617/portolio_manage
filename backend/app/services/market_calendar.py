"""거래일 캘린더 — market_holidays 기반.

`get_trading_days`(backtest_service)와 같은 테이블을 보지만 이쪽은 배치 스크립트가
"오늘 장이 섰나"를 묻는 용도다.
"""
import datetime

from sqlalchemy.orm import Session

from app.models.market_holiday import MarketHoliday


def is_market_holiday(db: Session, d: datetime.date | None = None) -> bool:
    """주말이거나 market_holidays에 등재된 날이면 True."""
    d = d or datetime.date.today()
    if d.weekday() >= 5:
        return True
    return db.query(MarketHoliday.date).filter(MarketHoliday.date == d).first() is not None


def resolve_batch_status(db: Session, status: str, d: datetime.date | None = None) -> str:
    """휴장일에 돈 배치의 status를 'holiday'로 바꾼다.

    왜 필요한가 — 휴장일엔 새 데이터가 없는 게 정상이라 배치가 아무것도 못 가져와도
    'success'로 남았다. 실제로 2026-08-17(광복절 대체휴일)에 refresh_etf_prices는
    `rows: 0, holidays: 1`을 요약에 적고도 success였고, refresh_benchmark_indices_bbg는
    빈 응답을 받아 'failed'로 남아 마치 블룸버그 PC가 꺼진 것처럼 보였다. 둘 다
    휴장일이라는 사실을 status에서 읽을 수 없어 사람이 매번 달력을 확인해야 했다.

    실패도 'holiday'로 덮지만 **error 텍스트는 그대로 둔다** — 휴장일의 빈 응답과
    진짜 장애를 나중에 구분할 수 있어야 하기 때문이다.
    """
    if status in ("success", "failed") and is_market_holiday(db, d):
        return "holiday"
    return status
