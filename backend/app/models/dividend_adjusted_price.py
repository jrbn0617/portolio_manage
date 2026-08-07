from datetime import date

from sqlalchemy import Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class DividendAdjustedPrice(Base):
    """prices(권리조정 수정주가) + dividends로 계산한 배당재투자 기준 수정종가.

    원본 시세가 아니라 파생 데이터이므로 prices와 분리해서 보관하고,
    prices/dividends가 갱신될 때마다 종목 단위로 재계산한다.
    """

    __tablename__ = "dividend_adjusted_prices"
    __table_args__ = (
        UniqueConstraint(
            "instrument_id", "date", "period", name="uq_div_adj_price_instrument_date_period"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    period: Mapped[str] = mapped_column(String(1), nullable=False)  # D | M
    adj_close: Mapped[float] = mapped_column(Numeric(18, 4), nullable=False)
