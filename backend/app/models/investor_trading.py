from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class InvestorTrading(Base):
    """투자자(기관/외국인/개인 등)별 일별 순매수 동향."""

    __tablename__ = "investor_trading"
    __table_args__ = (
        UniqueConstraint("instrument_id", "date", "investor_type", name="uq_investor_trading_instrument_date_type"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    investor_type: Mapped[str] = mapped_column(String(16), nullable=False)  # 기관합계 | 외국인 | 개인
    sell_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buy_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    sell_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    buy_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    net_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
