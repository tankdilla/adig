"""baseline

Revision ID: cb60f3280ea1
Revises: 
Create Date: 2026-02-20 14:34:28.974361

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'cb60f3280ea1'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "post_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("hook", sa.String(length=280), nullable=True),
        sa.Column("caption", sa.Text(), nullable=False),
        sa.Column("hashtags", sa.Text(), nullable=True),
        sa.Column("media_notes", sa.Text(), nullable=True),
        sa.Column("scheduled_for", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=280), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "creators",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("handle", sa.String(length=120), nullable=False, unique=True),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="instagram"),
        sa.Column("followers_est", sa.Integer(), nullable=True),
        sa.Column("niche_tags", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("score", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "outreach_campaigns",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=120), nullable=False, unique=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("offer_type", sa.String(length=64), nullable=True),
        sa.Column("campaign_name", sa.String(length=120), nullable=True),  # keep simple; later migrations can normalize
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("approved_by", sa.String(length=120), nullable=True),
        sa.Column("approved_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    op.create_table(
        "engagement_targets",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("target_url", sa.Text(), nullable=False, unique=True),
        sa.Column("target_handle", sa.String(length=120), nullable=True),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("source", sa.String(length=64), nullable=False, server_default="manual"),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("engagement_targets")
    op.drop_table("outreach_drafts")
    op.drop_table("outreach_campaigns")
    op.drop_table("creators")
    op.drop_table('post_drafts')
