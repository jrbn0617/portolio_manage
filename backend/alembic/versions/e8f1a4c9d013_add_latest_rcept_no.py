"""fund_risk_grades 에 latest_rcept_no 추가

캐시 키를 "읽어낸 공시"에서 "목록에서 본 최신 공시"로 바꾼다. 최신 투자설명서에 원본
파일이 없어 더 과거 건을 읽는 경우가 있는데(실측 — 신한골드 4건 연속 원본 없음),
읽어낸 접수번호로 비교하면 최신 건과 영영 어긋나 매번 다시 받는다.

Revision ID: e8f1a4c9d013
Revises: cd272b86b40d
"""
import sqlalchemy as sa
from alembic import op

revision = "e8f1a4c9d013"
down_revision = "cd272b86b40d"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("fund_risk_grades",
                  sa.Column("latest_rcept_no", sa.String(length=14), nullable=True))
    # 기존 행은 읽어낸 접수번호로 채워 둔다 — 다음 실행에서 최신값으로 갱신된다.
    op.execute("UPDATE fund_risk_grades SET latest_rcept_no = rcept_no")


def downgrade() -> None:
    op.drop_column("fund_risk_grades", "latest_rcept_no")
