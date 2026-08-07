from datetime import date

from pydantic import BaseModel, ConfigDict


class DividendRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    ex_date: date
    pay_date: date | None
    amount: float
