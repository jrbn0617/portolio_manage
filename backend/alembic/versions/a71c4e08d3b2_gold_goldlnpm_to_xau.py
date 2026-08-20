"""금 티커를 GOLDLNPM Index → XAU Curncy 로

GOLDLNPM(LBMA 런던 오후 고시가)은 블룸버그 응답에 PX_LAST 컬럼 자체가 오지 않았다 —
값이 비어 온 게 아니라 열이 없었다. 별도 라이선스 상품이라 터미널 엔타이틀먼트에
없는 것으로 보인다.

XAU Curncy 는 정상 응답한다(1920-01-30 ~, 13,915행). 값도 검증했다 —
1980-01-21 $850.00(1980년 정점), 2011-09-05 $1,900.20, 1920년 $20.68(금본위 공정가격).

뉴욕 마감 스냅이라 목록의 다른 미국물(S&P500 NTR, MSCI, US Treasury TR)과 시점이 맞는다.

Revision ID: a71c4e08d3b2
Revises: e5b207c41af3
"""
from alembic import op

revision = "a71c4e08d3b2"
down_revision = "e5b207c41af3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 이미 손으로 바꾼 DB 에서는 0행 — 그래도 문제없다.
    op.execute("""UPDATE bbg_indices SET bbg_ticker = 'XAU Curncy', ticker = 'XAU'
                  WHERE bbg_ticker = 'GOLDLNPM Index'""")


def downgrade() -> None:
    op.execute("""UPDATE bbg_indices SET bbg_ticker = 'GOLDLNPM Index', ticker = 'GOLDLNPM'
                  WHERE bbg_ticker = 'XAU Curncy'""")
