from datetime import date

from pydantic import BaseModel, ConfigDict


class DividendAdjustedPriceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    date: date
    period: str
    adj_close: float
