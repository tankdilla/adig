"""create intel engine

Revision ID: f8af46d2ad90
Revises: 2a6321c520b5
Create Date: 2026-02-26 19:45:16.772716

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f8af46d2ad90'
down_revision: Union[str, Sequence[str], None] = '2a6321c520b5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade():
    # Daily snapshots for growth tracking
    op.create_table(
        "creator_metrics_daily",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False, index=True),
        sa.Column("snapshot_date", sa.Date(), nullable=False, index=True),
        sa.Column("followers_est", sa.Integer(), nullable=True),
        sa.Column("posts_count", sa.Integer(), nullable=True),
        sa.Column("avg_like_est", sa.Integer(), nullable=True),
        sa.Column("avg_comment_est", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("creator_id", "snapshot_date", name="uq_creator_metrics_daily"),
    )

    # Niche evidence from posts/bio scanning
    op.create_table(
        "creator_signals",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False, index=True),
        sa.Column("signal_type", sa.String(length=32), nullable=False),  # "bio", "post", "hashtag"
        sa.Column("signal_text", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("1.0")),
        sa.Column("source_url", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    # Add lightweight summary columns to creators (optional but helpful)
    with op.batch_alter_table("creators") as batch:
        batch.add_column(sa.Column("niche_score", sa.Float(), nullable=True))
        batch.add_column(sa.Column("growth_7d", sa.Float(), nullable=True))
        batch.add_column(sa.Column("growth_30d", sa.Float(), nullable=True))
        batch.add_column(sa.Column("last_intel_run_at", sa.DateTime(), nullable=True))
        batch.add_column(sa.Column("is_partner", sa.Boolean(), server_default=sa.text("false"), nullable=False))  # your “best partners” seed flag


def downgrade():
    with op.batch_alter_table("creators") as batch:
        batch.drop_column("is_partner")
        batch.drop_column("last_intel_run_at")
        batch.drop_column("growth_30d")
        batch.drop_column("growth_7d")
        batch.drop_column("niche_score")

    op.drop_table("creator_signals")
    op.drop_table("creator_metrics_daily")
