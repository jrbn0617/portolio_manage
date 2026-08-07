from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class CorporateActionEvent(Base):
    """백테스트 중 보유종목의 가격 시계열이 도중에 끊긴 경우(상장폐지/인수합병)의
    사용자 분류 기록. 백테스트 엔진은 시계열 단절을 감지하면 우선 경고만 남기고
    끝까지 실행되고, 사용자가 별도로(resolve_backtest_event.py) 사유를 조사해
    입력하면 여기에 저장돼 이후 재실행부터 자동 반영된다.
    """

    __tablename__ = "corporate_action_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(
        ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    event_type: Mapped[str] = mapped_column(String(16), nullable=False)  # delisted | merger
    last_data_date: Mapped[date] = mapped_column(Date, nullable=False)  # 시계열이 끊긴(마지막 데이터) 날짜
    successor_instrument_id: Mapped[int | None] = mapped_column(
        ForeignKey("instruments.id", ondelete="SET NULL"), nullable=True
    )  # merger일 때만: 후속(존속) 종목
    exchange_ratio: Mapped[float | None] = mapped_column(Numeric(18, 6), nullable=True)  # merger: 원종목 1주당 후속종목 N주
    disposal_value: Mapped[float | None] = mapped_column(Numeric(18, 4), nullable=True)  # delisted: 최종 처분가치(주당)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
