"""지수관리 — 블룸버그에서 받아올 지수·환율 목록과 요청 방식.

가격 자체는 instruments(asset_type='index') + prices 에 들어가고, 여기서는 **수집 설정**만
다룬다. 목록에 적재 현황(행수·구간·최근값)을 붙여 주는 이유 — 설정만 봐서는 그 티커가
실제로 들어오고 있는지 알 수 없기 때문이다.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.bbg_index import BbgIndex
from app.schemas.bbg_index import BbgIndexCreate, BbgIndexRead, BbgIndexUpdate

router = APIRouter(prefix="/bbg-indices", tags=["bbg-indices"])

# 지수별 적재 현황. instruments 를 거쳐야 prices 와 이어진다.
_STATUS_SQL = text("""
    SELECT b.ticker, count(p.id), min(p.date), max(p.date)
    FROM bbg_indices b
    LEFT JOIN instruments i ON i.ticker = b.ticker AND i.asset_type = 'index'
    LEFT JOIN prices p ON p.instrument_id = i.id AND p.period = 'D'
    GROUP BY b.ticker
""")

_LAST_VALUE_SQL = text("""
    SELECT b.ticker, p.close
    FROM bbg_indices b
    JOIN instruments i ON i.ticker = b.ticker AND i.asset_type = 'index'
    JOIN LATERAL (SELECT close FROM prices WHERE instrument_id = i.id AND period = 'D'
                  ORDER BY date DESC LIMIT 1) p ON TRUE
""")


def _with_status(db: Session, rows: list[BbgIndex]) -> list[BbgIndexRead]:
    status = {r[0]: r for r in db.execute(_STATUS_SQL).all()}
    values = {r[0]: r[1] for r in db.execute(_LAST_VALUE_SQL).all()}
    out = []
    for r in rows:
        st = status.get(r.ticker)
        out.append(BbgIndexRead(
            id=r.id, bbg_ticker=r.bbg_ticker, ticker=r.ticker, name=r.name, note=r.note,
            refresh_mode=r.refresh_mode, fields=r.fields, compute_tr=r.compute_tr,
            start_date=r.start_date, enabled=r.enabled, sort_order=r.sort_order,
            rows=st[1] if st else 0, first_dt=st[2] if st else None,
            last_dt=st[3] if st else None,
            last_value=float(values[r.ticker]) if r.ticker in values
            and values[r.ticker] is not None else None,
            updated_at=r.updated_at))
    return out


@router.get("", response_model=list[BbgIndexRead], response_model_by_alias=True)
def list_bbg_indices(db: Session = Depends(get_db)):
    rows = db.query(BbgIndex).order_by(BbgIndex.sort_order, BbgIndex.ticker).all()
    return _with_status(db, rows)


@router.post("", response_model=BbgIndexRead, response_model_by_alias=True, status_code=201)
def create_bbg_index(payload: BbgIndexCreate, db: Session = Depends(get_db)):
    for col, val in (("bbg_ticker", payload.bbg_ticker), ("ticker", payload.ticker)):
        if db.query(BbgIndex).filter(getattr(BbgIndex, col) == val).first():
            raise HTTPException(status_code=409, detail=f"이미 등록된 {col} 입니다: {val}")
    # compute_tr(총수익 직접 계산)은 배당포인트 필드가 함께 필요하고 full 모드여야만 성립해서
    # 화면에서는 켜지 못하게 한다 — 코스피 계열 4종에만 쓰는 예외 경로다.
    row = BbgIndex(**payload.model_dump(), fields="PX_LAST", compute_tr=False, enabled=True)
    db.add(row)
    db.commit()
    db.refresh(row)
    return _with_status(db, [row])[0]


@router.patch("/{ticker}", response_model=BbgIndexRead, response_model_by_alias=True)
def update_bbg_index(ticker: str, payload: BbgIndexUpdate, db: Session = Depends(get_db)):
    row = db.query(BbgIndex).filter(BbgIndex.ticker == ticker).first()
    if not row:
        raise HTTPException(status_code=404, detail="등록되지 않은 지수입니다.")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("refresh_mode") == "daily" and row.compute_tr:
        raise HTTPException(
            status_code=400,
            detail="총수익을 직접 계산하는 지수는 전 구간(full)으로만 받을 수 있습니다.")
    for k, v in changes.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return _with_status(db, [row])[0]


@router.delete("/{ticker}", status_code=204)
def delete_bbg_index(ticker: str, db: Session = Depends(get_db)):
    """수집 설정만 지운다 — 이미 받아 둔 prices 는 건드리지 않는다.
    되살릴 때 이력이 그대로 남아 있어야 하고, 백테스트가 참조 중일 수도 있다."""
    row = db.query(BbgIndex).filter(BbgIndex.ticker == ticker).first()
    if not row:
        raise HTTPException(status_code=404, detail="등록되지 않은 지수입니다.")
    db.delete(row)
    db.commit()
