"""공모펀드 조회 API.

주식/ETF와 테이블이 분리돼 있어(funds/fund_navs/...) 라우터도 따로 둔다.
계층이 있다 — 운용펀드 하나 밑에 종류형(클래스) 펀드가 여럿 달리고, 보수 체계만
다르고 운용은 같다. 포트폴리오는 운용펀드 기준으로 만든 뒤 클래스로 매핑한다.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.fund import Fund, FundNav, FundSettlement
from app.schemas.fund import (FundClassRead, FundDetail, FundNavPoint, FundRead,
                              FundSettlementRead, FundStats)

router = APIRouter(prefix="/funds", tags=["funds"])


@router.get("/stats", response_model=FundStats)
def fund_stats(db: Session = Depends(get_db)):
    row = db.execute(text("""
        SELECT COUNT(*) total,
               COUNT(*) FILTER (WHERE is_manage_fund) mng,
               COUNT(*) FILTER (WHERE NOT is_manage_fund) cls,
               COUNT(*) FILTER (WHERE master_fund_code IS NULL) unmapped
        FROM funds""")).first()
    nav = db.execute(text(
        "SELECT COUNT(*), MIN(base_dt), MAX(base_dt), MAX(updated_at) FROM fund_navs")).first()
    return FundStats(total=row[0], manage_funds=row[1], class_funds=row[2], unmapped=row[3],
                     nav_rows=nav[0], nav_from=nav[1], nav_to=nav[2], updated_at=nav[3])


@router.get("/categories", response_model=list[str])
def fund_categories(db: Session = Depends(get_db)):
    return [r[0] for r in db.execute(text(
        "SELECT DISTINCT category FROM funds WHERE category IS NOT NULL ORDER BY 1"))]


@router.get("/companies", response_model=list[str])
def fund_companies(db: Session = Depends(get_db)):
    return [r[0] for r in db.execute(text(
        "SELECT DISTINCT manage_company FROM funds WHERE manage_company IS NOT NULL ORDER BY 1"))]


@router.get("", response_model=list[FundRead])
def list_funds(
    q: str | None = Query(None, description="펀드명 또는 펀드코드 부분일치"),
    category: str | None = None,
    manage_company: str | None = None,
    pension: str | None = Query(None, pattern="^(퇴직연금|개인연금|연금전체|일반)$",
                                description="연금 성격 필터. 연금전체=퇴직+개인, 일반=연금 아님"),
    special: bool | None = Query(None, description="구조가 특수한 펀드만/제외"),
    kind: str = Query("manage", pattern="^(manage|class|all)$",
                      description="manage=운용펀드만, class=클래스만, all=전체"),
    with_nav: bool = Query(True, description="기준가가 있는 펀드만"),
    limit: int = Query(200, le=1000),
    offset: int = 0,
    db: Session = Depends(get_db),
):
    """기본값이 운용펀드만인 이유 — 클래스까지 합치면 26,000건이라 목록으로는 못 본다.
    포트폴리오도 운용펀드 기준으로 만든다."""
    query = db.query(Fund)
    if kind == "manage":
        query = query.filter(Fund.is_manage_fund.is_(True))
    elif kind == "class":
        query = query.filter(Fund.is_manage_fund.is_(False))
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(Fund.name.ilike(like) | Fund.fund_code.ilike(like))
    if category:
        query = query.filter(Fund.category == category)
    if manage_company:
        query = query.filter(Fund.manage_company == manage_company)
    if pension == "연금전체":
        query = query.filter(Fund.pension_type.isnot(None))
    elif pension == "일반":
        query = query.filter(Fund.pension_type.is_(None))
    elif pension:
        query = query.filter(Fund.pension_type == pension)
    if special is not None:
        query = query.filter(Fund.special.is_(special))
    if with_nav:
        query = query.filter(db.query(FundNav.id).filter(FundNav.fund_id == Fund.id).exists())
    return query.order_by(Fund.name).offset(offset).limit(limit).all()


@router.get("/{fund_code}", response_model=FundDetail)
def get_fund(fund_code: str, db: Session = Depends(get_db)):
    fund = db.query(Fund).filter(Fund.fund_code == fund_code).first()
    if not fund:
        raise HTTPException(status_code=404, detail="펀드를 찾을 수 없습니다.")

    nav = db.query(func.min(FundNav.base_dt), func.max(FundNav.base_dt),
                   func.count(FundNav.id)).filter(FundNav.fund_id == fund.id).first()
    n_stl = db.query(func.count(FundSettlement.id)).filter(
        FundSettlement.fund_id == fund.id).scalar()

    # 같은 운용펀드에 속한 클래스들. 운용펀드 자신은 뺀다.
    classes = []
    if fund.master_fund_code:
        rows = db.execute(text("""
            SELECT f.fund_code, f.name, f.class_str, f.special, f.pension_type, f.incept_dt,
                   n.nav, n.base_dt
            FROM funds f
            LEFT JOIN LATERAL (SELECT nav, base_dt FROM fund_navs
                               WHERE fund_id = f.id ORDER BY base_dt DESC LIMIT 1) n ON TRUE
            WHERE f.master_fund_code = :m AND f.fund_code <> :m
            ORDER BY f.class_str NULLS LAST, f.name"""), {"m": fund.master_fund_code}).all()
        classes = [FundClassRead(fund_code=r[0], name=r[1], class_str=r[2], special=r[3],
                                 pension_type=r[4], incept_dt=r[5],
                                 last_nav=float(r[6]) if r[6] is not None else None,
                                 last_dt=r[7]) for r in rows]

    return FundDetail(**{c: getattr(fund, c) for c in
                         ("id", "fund_code", "name", "master_fund_code", "is_manage_fund",
                          "class_str", "special", "pension_type", "manage_company", "category",
                          "region",
                          "incept_dt", "custodian", "lead_dist", "term_dt")},
                      nav_from=nav[0], nav_to=nav[1], nav_count=nav[2],
                      settlement_count=n_stl, classes=classes)


@router.get("/{fund_code}/navs", response_model=list[FundNavPoint])
def get_fund_navs(
    fund_code: str,
    start: str | None = None,
    end: str | None = None,
    db: Session = Depends(get_db),
):
    """기준가와 수정기준가를 한 번에. 수정기준가는 결산으로 떨어진 기준가를 이어붙인
    총수익 계열이라, 성과를 보려면 이쪽을 써야 한다."""
    fund = db.query(Fund.id).filter(Fund.fund_code == fund_code).first()
    if not fund:
        raise HTTPException(status_code=404, detail="펀드를 찾을 수 없습니다.")
    sql = """
        SELECT v.base_dt, v.nav, a.adj_nav, a.adj_factor, v.aum
        FROM fund_navs v
        LEFT JOIN fund_adjusted_navs a ON a.fund_id = v.fund_id AND a.base_dt = v.base_dt
        WHERE v.fund_id = :fid
    """
    params = {"fid": fund[0]}
    if start:
        sql += " AND v.base_dt >= :start"
        params["start"] = start
    if end:
        sql += " AND v.base_dt <= :end"
        params["end"] = end
    sql += " ORDER BY v.base_dt"
    return [FundNavPoint(base_dt=r[0],
                         nav=float(r[1]) if r[1] is not None else None,
                         adj_nav=float(r[2]) if r[2] is not None else None,
                         adj_factor=float(r[3]) if r[3] is not None else None,
                         aum=r[4]) for r in db.execute(text(sql), params)]


@router.get("/{fund_code}/settlements", response_model=list[FundSettlementRead])
def get_fund_settlements(fund_code: str, db: Session = Depends(get_db)):
    fund = db.query(Fund.id).filter(Fund.fund_code == fund_code).first()
    if not fund:
        raise HTTPException(status_code=404, detail="펀드를 찾을 수 없습니다.")
    return (db.query(FundSettlement).filter(FundSettlement.fund_id == fund[0])
            .order_by(FundSettlement.period_end_value.desc()).all())
