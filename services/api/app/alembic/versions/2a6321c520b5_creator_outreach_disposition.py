"""creator outreach disposition

Revision ID: 2a6321c520b5
Revises: 86a04f2c6a77
Create Date: 2026-02-23 18:57:50.899746

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '2a6321c520b5'
down_revision: Union[str, Sequence[str], None] = '86a04f2c6a77'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column("creators", sa.Column("outreach_status", sa.String(length=32), nullable=False, server_default="eligible"))
    op.add_column("creators", sa.Column("outreach_exclude_reason", sa.Text(), nullable=True))

def downgrade() -> None:
    op.drop_column("creators", "outreach_exclude_reason")
    op.drop_column("creators", "outreach_status")
