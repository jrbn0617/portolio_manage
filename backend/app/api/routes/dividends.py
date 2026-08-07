from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.dividend import Dividend
from app.models.instrument import Instrument
from app.schemas.dividend import DividendRead

router = APIRouter(prefix="/dividends", tags=["dividends"])


@router.get("", response_model=list[DividendRead])
def list_dividends(ticker: str, db: Session = Depends(get_db)):
    return (
        db.query(Dividend)
        .join(Instrument)
        .filter(Instrument.ticker == ticker)
        .order_by(Dividend.ex_date)
        .all()
    )
