"""add shoot_pack and posted fields to post_drafts

Revision ID: cecdcaa1bfb4
Revises: cb60f3280ea1
Create Date: 2026-02-20 14:42:44.668384

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cecdcaa1bfb4'
down_revision: Union[str, Sequence[str], None] = 'cb60f3280ea1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("post_drafts", sa.Column("shoot_pack", sa.Text(), nullable=True))
    op.add_column("post_drafts", sa.Column("posted_at", sa.DateTime(), nullable=True))
    op.add_column("post_drafts", sa.Column("ig_url", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("post_drafts", "ig_url")
    op.drop_column("post_drafts", "posted_at")
    op.drop_column("post_drafts", "shoot_pack")
