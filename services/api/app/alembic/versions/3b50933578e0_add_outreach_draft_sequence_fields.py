"""add outreach draft sequence fields

Revision ID: 3b50933578e0
Revises: f8af46d2ad90
Create Date: 2026-03-25 20:18:43.088067

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3b50933578e0'
down_revision: Union[str, Sequence[str], None] = 'f8af46d2ad90'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade():
    op.add_column("outreach_drafts", sa.Column("sequence_name", sa.String(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("sequence_step", sa.Integer(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("due_at", sa.DateTime(), nullable=True))

def downgrade():
    op.drop_column("outreach_drafts", "due_at")
    op.drop_column("outreach_drafts", "sequence_step")
    op.drop_column("outreach_drafts", "sequence_name")
