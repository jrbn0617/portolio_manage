from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class IndexMarketCap(Base):
    """지수(KOSPI200 등) 편입 스냅샷 시점 기준 종목별 시가총액.

    prices 테이블과는 완전히 분리된 스냅샷 전용 테이블이다 — KOSPI 종목은
    2026-08-04 이전 가격 히스토리가 없어, 과거 스냅샷(2018~) 시가총액을
    prices에 끼워넣으면 파생 계산(월봉·배당조정지수)이 몇 년치 갭을
    "하루 등락률"로 오인해 깨질 수 있기 때문이다.
    """

    __tablename__ = "index_market_caps"
    __table_args__ = (
        UniqueConstraint(
            "index_name", "as_of_date", "instrument_id", name="uq_index_market_cap_name_date_instrument"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    index_name: Mapped[str] = mapped_column(String(32), nullable=False)  # KOSPI200 등
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)  # index_memberships.as_of_date와 동일 시점
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
    close: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    market_cap: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    volume: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    trading_value: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
