from datetime import date

from sqlalchemy import Date, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class MacroIndicator(Base):
    __tablename__ = "macro_indicators"
    __table_args__ = (
        UniqueConstraint("indicator_name", "date", name="uq_macro_indicator_name_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    indicator_name: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    value: Mapped[float] = mapped_column(Numeric(18, 6), nullable=False)
