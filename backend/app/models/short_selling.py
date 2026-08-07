from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class ShortSelling(Base):
    """일별 공매도 거래량/거래대금 (잔고는 pykrx 전종목 벌크 API가 없어 수집 대상 제외)."""

    __tablename__ = "short_selling"
    __table_args__ = (UniqueConstraint("instrument_id", "date", name="uq_short_selling_instrument_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    short_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
    short_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    total_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    value_ratio: Mapped[float | None] = mapped_column(Numeric(8, 4), nullable=True)
