from datetime import date

from pydantic import BaseModel, ConfigDict


class MacroIndicatorRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    indicator_name: str
    date: date
    value: float
