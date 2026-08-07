from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MonthlyFundamental(Base):
    """월 단위로만 갱신되는 종목별 펀더멘털 값(유동비율, 상장주식수 등)을
    범용적으로 저장한다. metric 문자열로 항목을 구분해서, 나중에 새 월간
    항목이 추가돼도 스키마 변경 없이 그대로 적재할 수 있다.

    prices/일별 파생계산과는 완전히 분리된 테이블이며, 특히 상장주식수는
    Instrument.shares_outstanding(분할 감지용, daily_update.py가 관리)과
    무관하게 별도로 쌓는 월별 이력이다.
    """

    __tablename__ = "monthly_fundamentals"
    __table_args__ = (
        UniqueConstraint("instrument_id", "date", "metric", name="uq_monthly_fundamental_instrument_date_metric"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)  # 월말 기준일
    metric: Mapped[str] = mapped_column(String(64), nullable=False)  # free_float_ratio | shares_outstanding_monthly | ...
    value: Mapped[float] = mapped_column(Numeric(24, 4), nullable=False)
