from datetime import date

from pydantic import BaseModel, ConfigDict


class MonthlyFundamentalRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    date: date
    metric: str
    value: float


class MonthlyFundamentalWithTicker(MonthlyFundamentalRead):
    ticker: str
    name: str


class MonthlyFundamentalUpsert(BaseModel):
    ticker: str
    date: date
    metric: str
    value: float
