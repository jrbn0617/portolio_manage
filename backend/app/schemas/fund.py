from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class FundRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fund_code: str
    name: str
    master_fund_code: str | None
    is_manage_fund: bool
    class_str: str | None
    special: bool
    manage_company: str | None
    category: str | None
    region: str | None
    incept_dt: date | None


class FundClassRead(BaseModel):
    """운용펀드 밑에 달린 종류형 펀드 요약."""
    model_config = ConfigDict(from_attributes=True)

    fund_code: str
    name: str
    class_str: str | None
    special: bool
    incept_dt: date | None
    last_nav: float | None
    last_dt: date | None


class FundDetail(FundRead):
    custodian: str | None
    lead_dist: str | None
    term_dt: date | None
    nav_from: date | None
    nav_to: date | None
    nav_count: int
    settlement_count: int
    classes: list[FundClassRead]


class FundNavPoint(BaseModel):
    base_dt: date
    nav: float | None
    adj_nav: float | None
    adj_factor: float | None
    aum: int | None


class FundSettlementRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    period_start_value: date | None
    period_end_value: date
    settlement_type: str
    nav: float | None
    post_settlement_nav: float | None
    ex_dividend_dt: date | None


class FundStats(BaseModel):
    """화면 상단 요약."""
    total: int
    manage_funds: int
    class_funds: int
    unmapped: int
    nav_rows: int
    nav_from: date | None
    nav_to: date | None
    updated_at: datetime | None
