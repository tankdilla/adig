"""add goals to outreach_campaigns

Revision ID: 86a04f2c6a77
Revises: 3062e02f9f78
Create Date: 2026-02-22 04:32:02.174732

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '86a04f2c6a77'
down_revision: Union[str, Sequence[str], None] = '3062e02f9f78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("outreach_campaigns", sa.Column("goal_outreaches", sa.Integer(), nullable=True))
    op.add_column("outreach_campaigns", sa.Column("goal_collabs", sa.Integer(), nullable=True))
    op.add_column("outreach_campaigns", sa.Column("notes", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("outreach_campaigns", "notes")
    op.drop_column("outreach_campaigns", "goal_collabs")
    op.drop_column("outreach_campaigns", "goal_outreaches")
