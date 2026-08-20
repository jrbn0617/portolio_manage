"""add bbg_indices table

블룸버그 수집 대상 지수를 스크립트 하드코딩에서 테이블로 옮긴다. 요청 방식(당일치/전 구간)이
티커마다 달라야 해서다. 기존 4개(코스피 계열)는 지금 동작 그대로 full 로 seed 한다.

Revision ID: e5b207c41af3
Revises: c3a91f4d2e70
"""
import datetime

import sqlalchemy as sa
from alembic import op

revision = "e5b207c41af3"
down_revision = "c3a91f4d2e70"
branch_labels = None
depends_on = None

TR_FIELDS = "PX_LAST,INDX_GROSS_DAILY_DIV"

# (bbg_ticker, ticker, name, mode, fields, compute_tr, start_date, sort)
SEED = [
    # 기존 배치가 하드코딩하고 있던 것들 — 동작을 그대로 옮긴다.
    ("KOSPI Index", "KOSPI", "코스피종합", "full", TR_FIELDS, True, datetime.date(2014, 1, 1), 10),
    ("KOSPI2 Index", "KOSPI200", "코스피200", "full", TR_FIELDS, True, datetime.date(2014, 1, 1), 11),
    ("KOSDAQ Index", "KOSDAQ", "코스닥종합", "full", TR_FIELDS, True, datetime.date(2014, 1, 1), 12),
    ("KOSDQ150 Index", "KOSDAQ150", "코스닥150", "full", TR_FIELDS, True, datetime.date(2014, 1, 1), 13),
    # 신규 — 이미 총수익/환율로 나오는 값이라 PX_LAST 하나만 받고 당일치만 갱신한다.
    ("LEGATRUU Index", "LEGATRUU", "Bloomberg Global-Aggregate TR Index",
     "daily", "PX_LAST", False, None, 20),
    ("GOLDLNPM Index", "GOLDLNPM", "Gold", "daily", "PX_LAST", False, None, 21),
    ("KOSPI2T Index", "KOSPI2T", "KOSPI2 TR", "daily", "PX_LAST", False, None, 22),
    ("NDUEACWF Index", "NDUEACWF", "MSCI ACWI NTR", "daily", "PX_LAST", False, None, 23),
    ("NDDUEAFE Index", "NDDUEAFE", "MSCI EAFE NTR", "daily", "PX_LAST", False, None, 24),
    ("NDUEEGF Index", "NDUEEGF", "MSCI EM NTR", "daily", "PX_LAST", False, None, 25),
    ("SPTR500N Index", "SPTR500N", "S&P500 NTR", "daily", "PX_LAST", False, None, 26),
    ("LT09TRUU Index", "LT09TRUU", "Bloomberg US Treasury 7-10 Year Total Return Index",
     "daily", "PX_LAST", False, None, 27),
    ("LT11TRUU Index", "LT11TRUU", "Bloomberg US Treasury 20+ Year Total Return Index",
     "daily", "PX_LAST", False, None, 28),
    ("LD20TRUU Index", "LD20TRUU", "Bloomberg US Treasury Bill Index",
     "daily", "PX_LAST", False, None, 29),
    ("USDKRW Curncy", "USDKRW", "USDKRW Currency", "daily", "PX_LAST", False, None, 30),
]


def upgrade() -> None:
    table = op.create_table(
        "bbg_indices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("bbg_ticker", sa.String(length=40), nullable=False),
        sa.Column("ticker", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("refresh_mode", sa.String(length=8), nullable=False, server_default="daily"),
        sa.Column("fields", sa.String(length=120), nullable=False, server_default="PX_LAST"),
        sa.Column("compute_tr", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("bbg_ticker", name="uq_bbg_indices_bbg_ticker"),
        sa.UniqueConstraint("ticker", name="uq_bbg_indices_ticker"),
    )
    op.bulk_insert(table, [
        dict(bbg_ticker=b, ticker=t, name=n, refresh_mode=m, fields=f,
             compute_tr=tr, start_date=sd, sort_order=so, enabled=True, note=None)
        for b, t, n, m, f, tr, sd, so in SEED
    ])


def downgrade() -> None:
    op.drop_table("bbg_indices")
