from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.instrument import Instrument
from app.models.price import Price
from app.schemas.price import PriceRead

router = APIRouter(prefix="/prices", tags=["prices"])


@router.get("", response_model=list[PriceRead])
def list_prices(
    ticker: str,
    period: str = "D",
    start: date | None = None,
    end: date | None = None,
    db: Session = Depends(get_db),
):
    query = (
        db.query(Price)
        .join(Instrument)
        .filter(Instrument.ticker == ticker, Price.period == period.upper())
    )
    if start:
        query = query.filter(Price.date >= start)
    if end:
        query = query.filter(Price.date <= end)
    return query.order_by(Price.date).all()
