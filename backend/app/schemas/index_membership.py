from datetime import date

from pydantic import BaseModel, ConfigDict


class IndexMembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    index_name: str
    as_of_date: date
    instrument_id: int


class IndexMembershipConstituent(BaseModel):
    instrument_id: int
    ticker: str
    name: str
    market: str | None = None
    sector: str | None = None
    krx_sector: str | None = None
    close: float | None = None
    market_cap: int | None = None
