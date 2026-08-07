from datetime import datetime

from pydantic import BaseModel, ConfigDict


class InstrumentBase(BaseModel):
    ticker: str
    name: str
    asset_type: str  # stock | etf | fund | index
    market: str | None = None
    sector: str | None = None
    industry: str | None = None


class InstrumentCreate(InstrumentBase):
    pass


class InstrumentUpdate(BaseModel):
    name: str | None = None
    asset_type: str | None = None
    market: str | None = None
    sector: str | None = None
    industry: str | None = None


class InstrumentRead(InstrumentBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
