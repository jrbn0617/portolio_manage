from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.instrument import Instrument
from app.models.monthly_fundamental import MonthlyFundamental
from app.schemas.monthly_fundamental import (
    MonthlyFundamentalUpsert,
    MonthlyFundamentalWithTicker,
)

router = APIRouter(prefix="/monthly-fundamentals", tags=["monthly-fundamentals"])


@router.get("", response_model=list[MonthlyFundamentalWithTicker])
def list_monthly_fundamentals(
    ticker: str | None = None,
    metric: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(MonthlyFundamental, Instrument.ticker, Instrument.name).join(
        Instrument, Instrument.id == MonthlyFundamental.instrument_id
    )
    if ticker:
        query = query.filter(Instrument.ticker == ticker)
    if metric:
        query = query.filter(MonthlyFundamental.metric == metric)
    rows = query.order_by(MonthlyFundamental.date.desc()).limit(500).all()
    return [
        MonthlyFundamentalWithTicker(
            id=mf.id, instrument_id=mf.instrument_id, date=mf.date, metric=mf.metric, value=float(mf.value),
            ticker=ticker_, name=name_,
        )
        for mf, ticker_, name_ in rows
    ]


@router.get("/metrics", response_model=list[str])
def list_metrics(db: Session = Depends(get_db)):
    rows = db.query(MonthlyFundamental.metric).distinct().order_by(MonthlyFundamental.metric).all()
    return [r[0] for r in rows]


@router.post("", response_model=MonthlyFundamentalWithTicker, status_code=201)
def upsert_monthly_fundamental(payload: MonthlyFundamentalUpsert, db: Session = Depends(get_db)):
    instrument = db.query(Instrument).filter(Instrument.ticker == payload.ticker).first()
    if instrument is None:
        raise HTTPException(status_code=404, detail=f"종목 {payload.ticker}를 찾을 수 없습니다.")

    row = (
        db.query(MonthlyFundamental)
        .filter(
            MonthlyFundamental.instrument_id == instrument.id,
            MonthlyFundamental.date == payload.date,
            MonthlyFundamental.metric == payload.metric,
        )
        .first()
    )
    if row:
        row.value = payload.value
    else:
        row = MonthlyFundamental(
            instrument_id=instrument.id, date=payload.date, metric=payload.metric, value=payload.value
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return MonthlyFundamentalWithTicker(
        id=row.id, instrument_id=row.instrument_id, date=row.date, metric=row.metric, value=float(row.value),
        ticker=instrument.ticker, name=instrument.name,
    )


@router.delete("/{entry_id}", status_code=204)
def delete_monthly_fundamental(entry_id: int, db: Session = Depends(get_db)):
    row = db.get(MonthlyFundamental, entry_id)
    if row is None:
        raise HTTPException(status_code=404, detail="항목을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
