from datetime import date

from pydantic import BaseModel, ConfigDict


class PriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    date: date
    period: str
    open: float | None
    high: float | None
    low: float | None
    close: float
    volume: int | None
