from datetime import date, datetime

from pydantic import BaseModel


class DataSourceRun(BaseModel):
    status: str
    started_at: datetime
    finished_at: datetime | None
    error: str | None


class DataSourceRead(BaseModel):
    key: str
    label: str
    source: str
    schedule: str
    job_name: str | None
    last_date: date | None
    date_label: str
    cadence: str  # daily | monthly | manual
    note: str | None
    last_run: DataSourceRun | None
    stale: bool
    pending: bool
    stale_reason: str | None
