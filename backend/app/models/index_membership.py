from datetime import date

from sqlalchemy import Date, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base


class IndexMembership(Base):
    __tablename__ = "index_memberships"
    __table_args__ = (
        UniqueConstraint(
            "index_name", "as_of_date", "instrument_id", name="uq_index_membership_name_date_instrument"
        ),
        Index("ix_index_membership_name_date", "index_name", "as_of_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    index_name: Mapped[str] = mapped_column(String(32), nullable=False)  # KOSPI, KOSDAQ, KOSPI200, KOSDAQ150 ...
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id", ondelete="CASCADE"), nullable=False)
