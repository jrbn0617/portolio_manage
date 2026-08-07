"""지수 편입 스냅샷(index_memberships) 시점 기준 시가총액을 KRX에서 받아
index_market_caps에 저장한다. backfill_index_market_caps.py(과거분)와
daily_update.py(향후 반기 스냅샷 생성 시)가 공유한다.
"""
import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models.index_market_cap import IndexMarketCap
from app.models.index_membership import IndexMembership
from app.models.market_holiday import MarketHoliday


def snap_to_trading_day(db: Session, as_of_date: datetime.date) -> datetime.date:
    """as_of_date가 실제 거래일이 아니면(주말/휴장일) 그 이하 마지막 실제 거래일을 반환한다.
    KRX에 비거래일을 그대로 조회하면 시가총액 등 값이 전부 0으로 나오므로 반드시 스냅해야 한다."""
    holidays = {r[0] for r in db.query(MarketHoliday.date).all()}
    day = as_of_date
    while day.weekday() >= 5 or day in holidays:
        day -= datetime.timedelta(days=1)
    return day


def fetch_and_store_index_market_caps(
    db: Session,
    stock_module,
    index_name: str,
    as_of_date: datetime.date,
    market: str,
    instruments_by_ticker: dict[str, int],
) -> int:
    """as_of_date(비거래일이면 스냅된 날짜)의 시가총액을 index_name 편입종목 기준으로
    조회해 upsert한다. 반환값은 적재된 행 수."""
    trading_day = snap_to_trading_day(db, as_of_date)

    member_tickers = {
        iid
        for (iid,) in db.query(IndexMembership.instrument_id)
        .filter(IndexMembership.index_name == index_name, IndexMembership.as_of_date == as_of_date)
        .all()
    }
    if not member_tickers:
        return 0

    df = stock_module.get_market_cap(trading_day.strftime("%Y%m%d"), market=market)
    if df is None or df.empty:
        return 0

    rows = []
    for ticker, r in df.iterrows():
        instrument_id = instruments_by_ticker.get(ticker)
        if instrument_id is None or instrument_id not in member_tickers:
            continue
        rows.append(
            dict(
                index_name=index_name,
                as_of_date=as_of_date,
                instrument_id=instrument_id,
                close=float(r["종가"]) or None,
                market_cap=int(r["시가총액"]) or None,
                shares_outstanding=int(r["상장주식수"]) if r["상장주식수"] else None,
                volume=int(r["거래량"]) or None,
                trading_value=int(r["거래대금"]) or None,
            )
        )

    if not rows:
        return 0

    stmt = pg_insert(IndexMarketCap).values(rows)
    stmt = stmt.on_conflict_do_update(
        index_elements=["index_name", "as_of_date", "instrument_id"],
        set_={
            "close": stmt.excluded.close,
            "market_cap": stmt.excluded.market_cap,
            "shares_outstanding": stmt.excluded.shares_outstanding,
            "volume": stmt.excluded.volume,
            "trading_value": stmt.excluded.trading_value,
        },
    )
    db.execute(stmt)
    db.commit()
    return len(rows)
