from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.instrument import Instrument
from app.schemas.instrument import InstrumentCreate, InstrumentRead, InstrumentUpdate

router = APIRouter(prefix="/instruments", tags=["instruments"])


@router.get("", response_model=list[InstrumentRead])
def list_instruments(asset_type: str | None = None, db: Session = Depends(get_db)):
    query = db.query(Instrument)
    if asset_type:
        query = query.filter(Instrument.asset_type == asset_type)
    return query.order_by(Instrument.ticker).all()


@router.post("", response_model=InstrumentRead, status_code=201)
def create_instrument(payload: InstrumentCreate, db: Session = Depends(get_db)):
    instrument = Instrument(**payload.model_dump())
    db.add(instrument)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 등록된 ticker입니다.") from exc
    db.refresh(instrument)
    return instrument


@router.patch("/{instrument_id}", response_model=InstrumentRead)
def update_instrument(instrument_id: int, payload: InstrumentUpdate, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(instrument, field, value)
    db.commit()
    db.refresh(instrument)
    return instrument


@router.delete("/{instrument_id}", status_code=204)
def delete_instrument(instrument_id: int, db: Session = Depends(get_db)):
    instrument = db.get(Instrument, instrument_id)
    if not instrument:
        raise HTTPException(status_code=404, detail="종목을 찾을 수 없습니다.")
    db.delete(instrument)
    db.commit()
