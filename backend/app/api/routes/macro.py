from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.macro_indicator import MacroIndicator
from app.schemas.macro_indicator import MacroIndicatorRead

router = APIRouter(prefix="/macro", tags=["macro"])


@router.get("", response_model=list[MacroIndicatorRead])
def list_macro_indicators(indicator_name: str, db: Session = Depends(get_db)):
    return (
        db.query(MacroIndicator)
        .filter(MacroIndicator.indicator_name == indicator_name)
        .order_by(MacroIndicator.date)
        .all()
    )
