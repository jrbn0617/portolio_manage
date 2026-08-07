from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class BatchRun(Base):
    """일일 갱신 등 배치 작업의 실행 이력. cron으로 실행되든 수동 트리거로
    실행되든(둘 다 결국 동일 스크립트를 실행) 여기에 기록된다."""

    __tablename__ = "batch_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_name: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)  # cron | manual
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="running")  # running|success|failed
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    log: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
