"""블룸버그에서 받아오는 지수·환율 목록. 어떤 티커를 어떤 방식으로 받을지를 담는다.

**왜 테이블로 빼나** — 요청 방식이 티커마다 다르기 때문이다. 코스피 계열은 배당포인트가
사후 정정돼서 매번 전 구간을 다시 받아 통째로 갈아끼워야 하지만, 이미 총수익으로 계산돼
나오는 해외지수는 당일치만 받으면 된다. 스크립트에 티커를 박아 두면 이 구분을 코드로
관리하게 되고, 지수를 하나 추가할 때마다 배포가 필요해진다.

가격은 기존 instruments(asset_type='index') + prices 에 그대로 넣는다 — 데이터 조회
화면과 백테스트가 이미 그 경로를 쓴다. 이 테이블은 **수집 설정**만 갖는다.
"""
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base

DAILY = "daily"   # 당일치만 (마지막 적재일 이후로 자동 보정)
FULL = "full"     # 전 구간 재수신 후 교체


class BbgIndex(Base):
    __tablename__ = "bbg_indices"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 블룸버그 티커 — 'LEGATRUU Index', 'USDKRW Curncy' 처럼 접미어까지 포함한 원문
    bbg_ticker: Mapped[str] = mapped_column(String(40), unique=True, nullable=False)
    # 우리 쪽 종목코드 (instruments.ticker). 보통 블룸버그 티커에서 접미어를 뗀 것
    ticker: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)

    # 화면에 뜨는 이름. 사용자가 고쳐 쓴다 — 나중에 찾기 쉽게 하는 게 목적이라 자유 문자열이다
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    refresh_mode: Mapped[str] = mapped_column(String(8), nullable=False, default=DAILY)
    # 요청 필드. 총수익을 직접 계산하는 지수만 배당포인트를 함께 받는다
    fields: Mapped[str] = mapped_column(String(120), nullable=False, default="PX_LAST")
    # True 면 PX_LAST + 배당포인트로 총수익지수를 만들어 prices.close 에 넣는다.
    # False 면 PX_LAST 가 곧 우리가 쓸 값이다 (이미 TR/NTR 로 나오는 티커).
    compute_tr: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=100)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
