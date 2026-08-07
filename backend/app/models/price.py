from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint, BigInteger
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Price(Base):
    __tablename__ = "prices"
    __table_args__ = (UniqueConstraint("instrument_id", "date", "period", name="uq_price_instrument_date_period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(String(1), nullable=False)  # D | M
    open: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    high: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    low: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    close: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # 미조정(실제 체결) 종가. close는 분할 등으로 소급조정된 값이라, 유동주식 시가총액처럼
    # "그 시점 실제 가격"이 필요한 계산엔 close 대신 이 컬럼을 써야 한다.
    raw_close: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)

    instrument: Mapped["Instrument"] = relationship(back_populates="prices")
