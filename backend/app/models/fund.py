"""국내 공모펀드 — 주식/ETF와 테이블을 분리한다.

왜 instruments/prices 를 재사용하지 않나 — 운용펀드만 추려도 기준가가 1,100만 행이라
현재 prices(887만 행)의 1.3배다. 한 테이블에 두면 주식 백테스트 질의가 펀드 행까지
스캔하게 되고, 키도 다르다(종목코드 6자리 vs 펀드코드 12자리).

계층 구조 — KOFIA 는 하나의 '운용펀드'(모펀드) 아래 여러 '종류형 펀드'(클래스, 자펀드)를
둔다. 클래스는 보수 체계만 다르고 운용은 같다. 포트폴리오는 운용펀드 기준으로 만든 뒤
클래스로 매핑해 나가므로, 두 계층을 같은 테이블에 담고 master_fund_code 로 잇는다.
"""
from datetime import date, datetime

from sqlalchemy import (BigInteger, Boolean, Date, DateTime, ForeignKey, Index,
                        Numeric, String, UniqueConstraint, func)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base_class import Base


class Fund(Base):
    """펀드 마스터. 운용펀드와 클래스 펀드가 함께 들어간다."""

    __tablename__ = "funds"
    __table_args__ = (Index("ix_funds_master_fund_code", "master_fund_code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_code: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)

    # 운용펀드 코드. 운용펀드 자신은 fund_code 와 같은 값을 갖는다(소스 DB 관례와 동일).
    # FK 로 걸지 않는 이유 — 증분 적재에서 자식이 부모보다 먼저 올 수 있다.
    master_fund_code: Mapped[str | None] = mapped_column(String(12), nullable=True)
    is_manage_fund: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 종류형 분류 결과. 운용펀드는 비어 있다.
    class_str: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 연금/퇴직/직판/레버리지 등 특수 목적 여부
    special: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    manage_company: Mapped[str | None] = mapped_column(String(50), nullable=True)
    category: Mapped[str | None] = mapped_column(String(50), nullable=True)
    region: Mapped[str | None] = mapped_column(String(20), nullable=True)
    custodian: Mapped[str | None] = mapped_column(String(50), nullable=True)
    lead_dist: Mapped[str | None] = mapped_column(String(50), nullable=True)
    incept_dt: Mapped[date | None] = mapped_column(Date, nullable=True)
    term_dt: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    navs: Mapped[list["FundNav"]] = relationship(back_populates="fund", cascade="all, delete-orphan")


class FundNav(Base):
    """일별 기준가. KOFIA fund_kr_kofia_daily_price 에 대응한다."""

    __tablename__ = "fund_navs"
    __table_args__ = (
        UniqueConstraint("fund_id", "base_dt", name="uq_fund_nav_fund_date"),
        Index("ix_fund_navs_base_dt", "base_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    base_dt: Mapped[date] = mapped_column(Date, nullable=False)
    nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    tax_base_nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    aum: Mapped[int | None] = mapped_column(BigInteger, nullable=True)  # 설정원본(백만원)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    fund: Mapped["Fund"] = relationship(back_populates="navs")


class FundSettlement(Base):
    """결산·상환·분배 이벤트. 기준가가 떨어지는 지점이라 수정기준가의 원천이다.

    ex_dividend_dt(회계기말 다음 영업일)에 기준가가 post_settlement_nav 로 리셋된다.
    주식의 배당락과 같은 성격이다."""

    __tablename__ = "fund_settlements"
    __table_args__ = (
        UniqueConstraint("fund_id", "period_end_value", "settlement_type",
                         name="uq_fund_settlement_fund_period_type"),
        Index("ix_fund_settlements_ex_dividend_dt", "ex_dividend_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    period_start_value: Mapped[date | None] = mapped_column(Date, nullable=True)  # 회계기초
    period_end_value: Mapped[date] = mapped_column(Date, nullable=False)          # 회계기말
    settlement_type: Mapped[str] = mapped_column(String(10), nullable=False)      # 결산 | 분배 | 상환
    elapsed_days: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    inception_principal: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)               # 결산 전 기준가
    tax_base_nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    post_settlement_nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)  # 결산 후
    ex_dividend_dt: Mapped[date | None] = mapped_column(Date, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class FundAdjustedNav(Base):
    """수정기준가 — 결산·분배로 떨어진 기준가를 이어붙인 총수익 계열.

    adj_factor = 누적(결산전 기준가 / 결산후 기준가), adj_nav = nav × adj_factor × 0.1.
    끝의 0.1 은 단위 환산이 아니라 **설정 시 100 기준으로 리베이스**하는 것이다
    (기준가는 설정 시 1,000원). 결산 이력이 없는 펀드에서 adj_nav/nav = 0.1 로 확인했다.
    주식·ETF 의 dividend_adjusted_prices 와 같은 역할이다."""

    __tablename__ = "fund_adjusted_navs"
    __table_args__ = (
        UniqueConstraint("fund_id", "base_dt", name="uq_fund_adj_nav_fund_date"),
        Index("ix_fund_adjusted_navs_base_dt", "base_dt"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id", ondelete="CASCADE"), nullable=False)
    base_dt: Mapped[date] = mapped_column(Date, nullable=False)
    nav: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)
    adj_nav: Mapped[float | None] = mapped_column(Numeric(18, 12), nullable=True)
    adj_factor: Mapped[float | None] = mapped_column(Numeric(15, 12), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
