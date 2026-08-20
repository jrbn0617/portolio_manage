# Alembic autogenerate가 모든 모델을 인식하도록 취합하는 모듈.
# 애플리케이션 런타임 코드(models/services/api)는 이 모듈이 아니라
# app.db.base_class.Base를 직접 import한다 (순환 import 방지).
from app.db.base_class import Base
from app.models.instrument import Instrument  # noqa: E402,F401
from app.models.price import Price  # noqa: E402,F401
from app.models.dividend import Dividend  # noqa: E402,F401
from app.models.macro_indicator import MacroIndicator  # noqa: E402,F401
from app.models.upload_batch import UploadBatch  # noqa: E402,F401
from app.models.index_membership import IndexMembership  # noqa: E402,F401
from app.models.dividend_adjusted_price import DividendAdjustedPrice  # noqa: E402,F401
from app.models.raw_close import RawClose  # noqa: E402,F401
from app.models.market_holiday import MarketHoliday  # noqa: E402,F401
from app.models.batch_run import BatchRun  # noqa: E402,F401
from app.models.investor_trading import InvestorTrading  # noqa: E402,F401
from app.models.short_selling import ShortSelling  # noqa: E402,F401
from app.models.index_market_cap import IndexMarketCap  # noqa: E402,F401
from app.models.corporate_action_event import CorporateActionEvent  # noqa: E402,F401
from app.models.monthly_fundamental import MonthlyFundamental  # noqa: E402,F401
from app.models.bbg_index import BbgIndex  # noqa: E402,F401
from app.models.fund_risk_grade import FundRiskGrade  # noqa: E402,F401
from app.models.fund import (  # noqa: E402,F401
    Fund, FundAdjustedNav, FundNav, FundSettlement,
)

__all__ = ["Base"]
