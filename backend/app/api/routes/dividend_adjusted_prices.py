from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dividend_adjusted_price import DividendAdjustedPrice
from app.models.instrument import Instrument
from app.schemas.dividend_adjusted_price import DividendAdjustedPriceRead

router = APIRouter(prefix="/dividend-adjusted-prices", tags=["dividend-adjusted-prices"])


@router.get("", response_model=list[DividendAdjustedPriceRead])
def list_dividend_adjusted_prices(
    ticker: str,
    period: str = "D",
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(DividendAdjustedPrice)
        .join(Instrument)
        .filter(Instrument.ticker == ticker, DividendAdjustedPrice.period == period.upper())
    )
    if start:
        query = query.filter(DividendAdjustedPrice.date >= start)
    if end:
        query = query.filter(DividendAdjustedPrice.date <= end)
    return query.order_by(DividendAdjustedPrice.date).all()
