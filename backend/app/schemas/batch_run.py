from datetime import datetime

from pydantic import BaseModel, ConfigDict


class BatchRunRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_name: str
    trigger: str
    status: str
    started_at: datetime
    finished_at: datetime | None
    summary: str | None
    log: str | None
    error: str | None


class BatchScheduleRead(BaseModel):
    job_name: str
    description: str
    source: str
    cron: str | None  # 수동 업로드 항목은 cron이 없다
    schedule: str
    timezone: str
    runnable: bool
