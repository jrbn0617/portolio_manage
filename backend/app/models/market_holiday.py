from datetime import date

from sqlalchemy import Date, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MarketHoliday(Base):
    """KRX(코스피/코스닥 공통) 휴장일. 백테스팅 시 거래일 캘린더로도 재사용한다."""

    __tablename__ = "market_holidays"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    name: Mapped[str | None] = mapped_column(String(64), nullable=True)
