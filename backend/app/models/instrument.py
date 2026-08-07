from datetime import datetime

from sqlalchemy import BigInteger, String, DateTime, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Instrument(Base):
    __tablename__ = "instruments"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticker: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    asset_type: Mapped[str] = mapped_column(String(16), nullable=False)  # stock | etf | fund | index
    market: Mapped[str | None] = mapped_column(String(32), nullable=True)
    sector: Mapped[str | None] = mapped_column(String(64), nullable=True)
    industry: Mapped[str | None] = mapped_column(String(64), nullable=True)
    krx_sector: Mapped[str | None] = mapped_column(String(64), nullable=True)  # KRX 자체 업종분류
    # 권리락(액면분할/무상증자/유상증자/소각 등) 이벤트 감지용 — pykrx로 주기 점검해 값이
    # 바뀌면 해당 종목의 가격을 재수집한다.
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    prices: Mapped[list["Price"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")
    dividends: Mapped[list["Dividend"]] = relationship(back_populates="instrument", cascade="all, delete-orphan")
