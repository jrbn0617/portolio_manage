"""add pension_type to funds

연금·퇴직은 special(구조가 특수해 비교가 어려운 펀드)에서 빼고 별도 축으로 분리한다.
배제 대상이 아니라 연금·퇴직연금 포트폴리오의 **유니버스가 되는 선택 축**이기 때문이다.

Revision ID: c3a91f4d2e70
Revises: 1d7b0e987dd6
"""
from alembic import op
import sqlalchemy as sa

revision = "c3a91f4d2e70"
down_revision = "1d7b0e987dd6"
branch_labels = None
depends_on = None

# app.services.fund_classify 와 같은 규칙. 마이그레이션은 코드 변경에 따라가지 않도록
# 여기에 값을 박아 둔다 — 나중에 키워드가 바뀌어도 이 리비전의 결과는 재현된다.
SPECIAL_KEYWORDS = ["주택", "소득공제", "전환형", "직판",
                    "적립식", "목표전환", "레버리지", "인버스", "월지급"]


def upgrade() -> None:
    op.add_column("funds", sa.Column("pension_type", sa.String(length=10), nullable=True))
    op.create_index("ix_funds_pension_type", "funds", ["pension_type"])

    # '퇴직연금'은 두 키워드를 다 갖고 있어 순서가 곧 우선순위다 — 퇴직이 이긴다.
    op.execute("""
        UPDATE funds SET pension_type =
            CASE WHEN name LIKE '%퇴직%' THEN '퇴직연금'
                 WHEN name LIKE '%연금%' THEN '개인연금' END
        WHERE name LIKE '%퇴직%' OR name LIKE '%연금%'
    """)

    cond = " OR ".join(f"name LIKE '%{k}%'" for k in SPECIAL_KEYWORDS)
    op.execute(f"UPDATE funds SET special = ({cond})")


def downgrade() -> None:
    # special 을 옛 규칙(연금·퇴직 포함)으로 되돌린다.
    cond = " OR ".join(f"name LIKE '%{k}%'" for k in SPECIAL_KEYWORDS + ["연금", "퇴직"])
    op.execute(f"UPDATE funds SET special = ({cond})")
    op.drop_index("ix_funds_pension_type", table_name="funds")
    op.drop_column("funds", "pension_type")
