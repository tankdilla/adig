"""outreach agent tables and tracking

Revision ID: 205bb6c05117
Revises: c77c5059fff2
Create Date: 2026-02-20 20:52:49.875538

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '205bb6c05117'
down_revision: Union[str, Sequence[str], None] = 'c77c5059fff2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1) enum type for outreachstatus (Postgres)
    outreachstatus = sa.Enum(
        "pending", "approved", "sent", "replied", "booked", "declined", "ghosted",
        name="outreachstatus",
    )
    outreachstatus.create(op.get_bind(), checkfirst=True)

    # 2) outreach_campaigns
    # op.create_table(
    #     "outreach_campaigns",
    #     sa.Column("id", sa.Integer(), primary_key=True),
    #     sa.Column("name", sa.String(length=120), nullable=False),
    #     sa.Column("goal_outreaches", sa.Integer(), nullable=False, server_default="20"),
    #     sa.Column("goal_collabs", sa.Integer(), nullable=False, server_default="5"),
    #     sa.Column("notes", sa.Text(), nullable=True),
    #     sa.Column("created_at", sa.DateTime(), nullable=False),
    # )

    # 3) outreach_events
    op.create_table(
        "outreach_events",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("outreach_draft_id", sa.Integer(), sa.ForeignKey("outreach_drafts.id"), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_outreach_events_outreach_draft_id", "outreach_events", ["outreach_draft_id"])

    # 4) add columns to outreach_drafts
    op.add_column("outreach_drafts", sa.Column("outreach_status", outreachstatus, nullable=False, server_default="pending"))
    op.add_column("outreach_drafts", sa.Column("campaign_id", sa.Integer(), sa.ForeignKey("outreach_campaigns.id"), nullable=True))
    op.add_column("outreach_drafts", sa.Column("send_channel", sa.String(length=32), nullable=False, server_default="instagram_dm"))
    op.add_column("outreach_drafts", sa.Column("sent_by", sa.String(length=120), nullable=True))
    op.add_column("outreach_drafts", sa.Column("sent_at", sa.DateTime(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("thread_url", sa.Text(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("last_response_at", sa.DateTime(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("last_response_text", sa.Text(), nullable=True))
    op.add_column("outreach_drafts", sa.Column("followups_sent", sa.Integer(), nullable=False, server_default="0"))

    # optional helpful indexes
    op.create_index("ix_outreach_drafts_outreach_status", "outreach_drafts", ["outreach_status"])
    op.create_index("ix_outreach_drafts_campaign_id", "outreach_drafts", ["campaign_id"])


def downgrade():
    op.drop_index("ix_outreach_drafts_campaign_id", table_name="outreach_drafts")
    op.drop_index("ix_outreach_drafts_outreach_status", table_name="outreach_drafts")

    op.drop_column("outreach_drafts", "followups_sent")
    op.drop_column("outreach_drafts", "last_response_text")
    op.drop_column("outreach_drafts", "last_response_at")
    op.drop_column("outreach_drafts", "thread_url")
    op.drop_column("outreach_drafts", "sent_at")
    op.drop_column("outreach_drafts", "sent_by")
    op.drop_column("outreach_drafts", "send_channel")
    op.drop_column("outreach_drafts", "campaign_id")
    op.drop_column("outreach_drafts", "outreach_status")

    op.drop_index("ix_outreach_events_outreach_draft_id", table_name="outreach_events")
    op.drop_table("outreach_events")
    # op.drop_table("outreach_campaigns")

    outreachstatus = sa.Enum(
        "pending", "approved", "sent", "replied", "booked", "declined", "ghosted",
        name="outreachstatus",
    )
    outreachstatus.drop(op.get_bind(), checkfirst=True)
