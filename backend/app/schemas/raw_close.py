from datetime import date

from pydantic import BaseModel, ConfigDict


class RawCloseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    instrument_id: int
    date: date
    close: float
