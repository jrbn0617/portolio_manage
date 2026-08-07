from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.index_market_cap import IndexMarketCap
from app.models.index_membership import IndexMembership
from app.models.instrument import Instrument
from app.schemas.index_membership import IndexMembershipConstituent, IndexMembershipRead

router = APIRouter(prefix="/index-memberships", tags=["index-memberships"])


@router.get("", response_model=list[IndexMembershipRead])
def list_index_memberships(
    index_name: str,
    ticker: str | None = None,
    db: Session = Depends(get_db),
):
    query = db.query(IndexMembership).filter(IndexMembership.index_name == index_name)
    if ticker:
        query = query.join(Instrument).filter(Instrument.ticker == ticker)
    return query.order_by(IndexMembership.as_of_date).all()


@router.get("/index-names", response_model=list[str])
def list_index_names(db: Session = Depends(get_db)):
    rows = db.query(IndexMembership.index_name).distinct().order_by(IndexMembership.index_name).all()
    return [r[0] for r in rows]


@router.get("/snapshots", response_model=list[date])
def list_snapshots(index_name: str, db: Session = Depends(get_db)):
    rows = (
        db.query(IndexMembership.as_of_date)
        .filter(IndexMembership.index_name == index_name)
        .distinct()
        .order_by(IndexMembership.as_of_date.desc())
        .all()
    )
    return [r[0] for r in rows]


@router.get("/constituents", response_model=list[IndexMembershipConstituent])
def list_constituents(index_name: str, as_of_date: date, db: Session = Depends(get_db)):
    rows = (
        db.query(Instrument, IndexMarketCap)
        .join(IndexMembership, IndexMembership.instrument_id == Instrument.id)
        .outerjoin(
            IndexMarketCap,
            (IndexMarketCap.index_name == IndexMembership.index_name)
            & (IndexMarketCap.as_of_date == IndexMembership.as_of_date)
            & (IndexMarketCap.instrument_id == Instrument.id),
        )
        .filter(IndexMembership.index_name == index_name, IndexMembership.as_of_date == as_of_date)
        .all()
    )
    result = [
        IndexMembershipConstituent(
            instrument_id=inst.id,
            ticker=inst.ticker,
            name=inst.name,
            market=inst.market,
            sector=inst.sector,
            krx_sector=inst.krx_sector,
            close=float(cap.close) if cap and cap.close is not None else None,
            market_cap=cap.market_cap if cap else None,
        )
        for inst, cap in rows
    ]
    result.sort(key=lambda r: (r.market_cap is None, -(r.market_cap or 0), r.ticker))
    return result
