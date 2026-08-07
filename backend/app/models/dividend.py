from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Dividend(Base):
    __tablename__ = "dividends"
    __table_args__ = (UniqueConstraint("instrument_id", "ex_date", name="uq_dividend_instrument_ex_date"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    pay_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    amount: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)

    instrument: Mapped["Instrument"] = relationship(back_populates="dividends")
