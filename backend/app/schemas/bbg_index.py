from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class BbgIndexRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    bbg_ticker: str
    ticker: str
    name: str
    note: str | None
    refresh_mode: str
    fields_: str = Field(alias="fields")          # pydantic BaseModel.fields 와 충돌해 별칭을 쓴다
    compute_tr: bool
    start_date: date | None
    enabled: bool
    sort_order: int
    # 실제 적재 현황 — 설정만 봐서는 데이터가 들어오고 있는지 알 수 없다
    rows: int = 0
    first_dt: date | None = None
    last_dt: date | None = None
    last_value: float | None = None
    updated_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class BbgIndexUpdate(BaseModel):
    """화면에서 고칠 수 있는 것들. 티커는 바꾸지 못한다 — prices 가 그 값으로 붙어 있다."""

    name: str | None = None
    note: str | None = None
    refresh_mode: str | None = Field(None, pattern="^(daily|full)$")
    start_date: date | None = None
    enabled: bool | None = None
    sort_order: int | None = None


class BbgIndexCreate(BaseModel):
    bbg_ticker: str
    ticker: str
    name: str
    note: str | None = None
    refresh_mode: str = Field("daily", pattern="^(daily|full)$")
    start_date: date | None = None
    sort_order: int = 100
