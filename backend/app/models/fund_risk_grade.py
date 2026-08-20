"""펀드 투자위험등급 — DART 투자설명서에서 읽어 캐시한다.

투자설명서는 정정될 때만 나오므로 **접수번호(rcept_no)가 캐시 키**다. 같은 접수번호면
다시 읽지 않고, 새 접수건이 뜨면 갱신한다. 등급은 운용실적·시장상황에 따라 바뀌므로
`checked_at` 으로 마지막 확인 시점을 남긴다.
"""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class FundRiskGrade(Base):
    __tablename__ = "fund_risk_grades"

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_code: Mapped[str] = mapped_column(String(12), unique=True, index=True, nullable=False)

    # 1(매우높은위험) ~ 6(매우낮은위험). DART 문서에 숫자가 안 보이면 None 이다.
    risk_grade: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_label: Mapped[str | None] = mapped_column(String(40), nullable=True)

    # 실제로 읽어낸 공시
    rcept_no: Mapped[str | None] = mapped_column(String(14), nullable=True, index=True)
    # **캐시 키는 이쪽이다.** 목록에서 본 가장 최신 투자설명서의 접수번호.
    # 최신 건에 원본 파일이 없어 더 과거 건을 읽는 경우가 있어(실측 — 신한골드는 4건
    # 연속 원본 없음) rcept_no 로 비교하면 매번 다시 받게 된다.
    latest_rcept_no: Mapped[str | None] = mapped_column(String(14), nullable=True)
    rcept_dt: Mapped[date | None] = mapped_column(Date, nullable=True)
    report_nm: Mapped[str | None] = mapped_column(String(300), nullable=True)
    # DART 쪽 펀드명 — 우리 이름과 표기가 달라 매칭 근거를 남겨 둔다
    dart_fund_name: Mapped[str | None] = mapped_column(String(300), nullable=True)

    # 못 찾았을 때 왜인지. 조용히 비워 두면 다음에 원인을 다시 파야 한다.
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
