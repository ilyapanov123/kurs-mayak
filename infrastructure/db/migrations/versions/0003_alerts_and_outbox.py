"""Create alerts and notification_outbox (DB-03).

Revision ID: 0003_alerts_and_outbox
Revises: 0002_quotes_storage
Create Date: 2026-11-26
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0003_alerts_and_outbox"
down_revision = "0002_quotes_storage"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pair_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("condition", sa.String(length=2), nullable=False),
        sa.Column("threshold", sa.Numeric(), nullable=False),
        sa.Column("active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_match", sa.Boolean(), nullable=True),
        sa.Column("last_quote_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trigger_seq", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("version", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("condition IN ('ge', 'le')", name="ck_alerts_condition"),
        sa.CheckConstraint("threshold > 0", name="ck_alerts_threshold_positive"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"],
                                name="fk_alerts_user_id_users", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["pair_id"], ["pairs.id"],
                                name="fk_alerts_pair_id_pairs", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_alerts"),
    )
    op.create_index("ix_alerts_user_created", "alerts", ["user_id", "created_at"])
    op.create_index("ix_alerts_active_pair", "alerts", ["pair_id"],
                    postgresql_where=sa.text("active"))

    op.create_table(
        "notification_outbox",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("alert_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("trigger_seq", sa.BigInteger(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=12),
                  server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("last_error_code", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('pending','sending','sent','retry','failed','cancelled','unknown')",
            name="ck_outbox_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_outbox_attempts_non_negative"),
        sa.ForeignKeyConstraint(["alert_id"], ["alerts.id"],
                                name="fk_outbox_alert_id_alerts", ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_notification_outbox"),
        sa.UniqueConstraint("alert_id", "trigger_seq", name="uq_outbox_alert_trigger_seq"),
    )
    op.create_index("ix_outbox_due", "notification_outbox", ["next_attempt_at"],
                    postgresql_where=sa.text("status IN ('pending','retry')"))


def downgrade() -> None:
    op.drop_index("ix_outbox_due", table_name="notification_outbox")
    op.drop_table("notification_outbox")
    op.drop_index("ix_alerts_active_pair", table_name="alerts")
    op.drop_index("ix_alerts_user_created", table_name="alerts")
    op.drop_table("alerts")