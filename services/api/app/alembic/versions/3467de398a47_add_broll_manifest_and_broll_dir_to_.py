"""add broll_manifest and broll_dir to post_drafts

Revision ID: 3467de398a47
Revises: cecdcaa1bfb4
Create Date: 2026-02-20 17:55:36.388237

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '3467de398a47'
down_revision: Union[str, Sequence[str], None] = 'cecdcaa1bfb4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("post_drafts", sa.Column("broll_manifest", sa.Text(), nullable=True))
    op.add_column("post_drafts", sa.Column("broll_dir", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("post_drafts", "broll_manifest")
    op.drop_column("post_drafts", "broll_dir")
