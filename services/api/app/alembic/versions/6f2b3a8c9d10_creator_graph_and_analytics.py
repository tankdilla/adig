"""creator graph and analytics

Revision ID: 6f2b3a8c9d10
Revises: c77c5059fff2
Create Date: 2026-02-21

"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "6f2b3a8c9d10"
down_revision = "c77c5059fff2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ---- creators: add analytics + fraud columns ----
    op.add_column("creators", sa.Column("posts_count", sa.Integer(), nullable=True))
    op.add_column("creators", sa.Column("avg_engagement_rate", sa.Float(), nullable=True))
    op.add_column("creators", sa.Column("avg_like_count", sa.Integer(), nullable=True))
    op.add_column("creators", sa.Column("avg_comment_count", sa.Integer(), nullable=True))
    op.add_column("creators", sa.Column("is_brand", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("creators", sa.Column("is_spam", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("creators", sa.Column("fraud_score", sa.Integer(), server_default=sa.text("0"), nullable=False))
    op.add_column("creators", sa.Column("fraud_flags", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("creators", sa.Column("last_scraped_at", sa.DateTime(), nullable=True))

    # Ensure enum types exist (safe across branches)
    # creator_relationship_status = postgresql.ENUM(
    #     'new', 'contacted', 'replied', 'partnered', 'declined', 'blocked',
    #     name='creatorrelationshipstatus',
    # )
    # creator_edge_type = postgresql.ENUM(
    #     'mention', 'co_mentioned', 'similarity', 'audience_overlap',
    #     name='creatoredgetype',
    # )

    creator_relationship_status = postgresql.ENUM(
        "new", "contacted", "replied", "partnered", "declined", "blocked",
        name="creatorrelationshipstatus",
        create_type=False,  # <- critical: prevents CREATE TYPE during create_table
    )
    creator_edge_type = postgresql.ENUM(
        "mention", "co_mentioned", "similarity", "audience_overlap",
        name="creatoredgetype",
        create_type=False,  # <- critical
    )

    bind = op.get_bind()
    creator_relationship_status.create(bind, checkfirst=True)
    creator_edge_type.create(bind, checkfirst=True)

    # ---- creator_relationships ----
    op.create_table(
        "creator_relationships",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False, unique=True),
        sa.Column("status", creator_relationship_status, nullable=False, server_default="new"),
        sa.Column("last_contacted_at", sa.DateTime(), nullable=True),
        sa.Column("last_campaign_id", sa.Integer(), sa.ForeignKey("outreach_campaigns.id"), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    # ---- creator_edges ----
    op.create_table(
        "creator_edges",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("source_creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("target_creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("edge_type", creator_edge_type, nullable=False),
        sa.Column("weight", sa.Float(), nullable=False, server_default=sa.text("0")),
        sa.Column("metadata", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("source_creator_id", "target_creator_id", "edge_type", name="uq_creator_edges"),
    )

    # ---- creator_posts ----
    op.create_table(
        "creator_posts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("creator_id", sa.Integer(), sa.ForeignKey("creators.id"), nullable=False),
        sa.Column("platform", sa.String(length=32), nullable=False, server_default="instagram"),
        sa.Column("post_url", sa.Text(), nullable=False, unique=True),
        sa.Column("post_type", sa.String(length=32), nullable=False, server_default="reel"),
        sa.Column("caption", sa.Text(), nullable=True),
        sa.Column("posted_at", sa.DateTime(), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("extracted", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
    )

    # ---- viral_pattern_reports ----
    op.create_table(
        "viral_pattern_reports",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("report_date", sa.String(length=10), nullable=False),
        sa.Column("scope", sa.String(length=64), nullable=False, server_default="instagram"),
        sa.Column("report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("report_date", "scope", name="uq_viral_pattern_reports"),
    )


def downgrade() -> None:
    op.drop_table("viral_pattern_reports")
    op.drop_table("creator_posts")
    op.drop_table("creator_edges")
    op.drop_table("creator_relationships")

    op.drop_column("creators", "last_scraped_at")
    op.drop_column("creators", "fraud_flags")
    op.drop_column("creators", "fraud_score")
    op.drop_column("creators", "is_spam")
    op.drop_column("creators", "is_brand")
    op.drop_column("creators", "avg_comment_count")
    op.drop_column("creators", "avg_like_count")
    op.drop_column("creators", "avg_engagement_rate")
    op.drop_column("creators", "posts_count")

    bind = op.get_bind()

    creator_relationship_status = postgresql.ENUM(
        "new", "contacted", "replied", "partnered", "declined", "blocked",
        name="creatorrelationshipstatus",
        create_type=False,
    )
    creator_edge_type = postgresql.ENUM(
        "mention", "co_mentioned", "similarity", "audience_overlap",
        name="creatoredgetype",
        create_type=False,
    )
    creator_edge_type.drop(bind, checkfirst=True)
    creator_relationship_status.drop(bind, checkfirst=True)
