from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class RawClose(Base):
    """수정(권리조정)되지 않은 실제 종가 캐시.

    배당조정 수정주가 지수 계산 시, 배당금(분할 등이 반영되지 않은 원본 금액)과
    같은 기준으로 비교할 "그 날의 실제 종가"가 필요할 때만 채워 넣는다
    (전체 히스토리가 아니라 배당이 있는 날짜만).
    """

    __tablename__ = "raw_closes"
    __table_args__ = (UniqueConstraint("instrument_id", "date", name="uq_raw_close_instrument_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    close: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
