"""SQLAlchemy mappings for users, dialog sessions, catalog assets, pairs and quotes."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Index,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("telegram_user_id", name="uq_users_telegram_user_id"),)

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class DialogSessionModel(Base):
    """At most one persistent dialog session per user; version supports optimistic locking."""

    __tablename__ = "dialog_sessions"
    __table_args__ = (
        CheckConstraint("version >= 1", name="ck_dialog_sessions_version_positive"),
    )

    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE", name="fk_dialog_sessions_user_id_users"), primary_key=True
    )
    state: Mapped[str] = mapped_column(String(40), nullable=False, server_default=text("'idle'"))
    payload: Mapped[dict[str, object]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("1"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class AssetModel(Base):
    __tablename__ = "assets"
    __table_args__ = (CheckConstraint("kind IN ('fiat', 'crypto')", name="ck_assets_kind"),)

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    kind: Mapped[str] = mapped_column(String(6), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class PairModel(Base):
    __tablename__ = "pairs"
    __table_args__ = (
        UniqueConstraint("base_code", "quote_code", name="uq_pairs_base_quote"),
        CheckConstraint("base_code <> quote_code", name="ck_pairs_distinct_assets"),
        CheckConstraint("kind IN ('fiat', 'crypto')", name="ck_pairs_kind"),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    base_code: Mapped[str] = mapped_column(ForeignKey("assets.code", name="fk_pairs_base_code_assets"), nullable=False)
    quote_code: Mapped[str] = mapped_column(ForeignKey("assets.code", name="fk_pairs_quote_code_assets"), nullable=False)
    kind: Mapped[str] = mapped_column(String(6), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, server_default=text("true"))


class QuoteModel(Base):
    """A point in one independent time series (live or hourly)."""

    __tablename__ = "quotes"
    __table_args__ = (
        CheckConstraint("series_kind IN ('live', 'hour')", name="ck_quotes_series_kind"),
        CheckConstraint("price > 0", name="ck_quotes_price_positive"),
        Index("ix_quotes_pair_series_quoted_at", "pair_id", "series_kind", "quoted_at"),
    )

    pair_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("pairs.id", ondelete="CASCADE", name="fk_quotes_pair_id_pairs"), primary_key=True
    )
    source: Mapped[str] = mapped_column(Text, primary_key=True)
    series_kind: Mapped[str] = mapped_column(String(4), primary_key=True)
    quoted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # No precision/scale is specified intentionally: PostgreSQL NUMERIC stays exact.
    price: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)

class AlertModel(Base):
    """Пороговое уведомление пользователя (HLD 6.1, F_Alert_1–24).

    Состояние автомата (last_match, trigger_seq) хранится здесь —
    это обеспечивает NF_Reliab_2: перезапуск не создаёт повторных событий.
    """

    __tablename__ = "alerts"
    __table_args__ = (
        CheckConstraint("condition IN ('ge', 'le')", name="ck_alerts_condition"),
        CheckConstraint("threshold > 0", name="ck_alerts_threshold_positive"),
        Index("ix_alerts_user_created", "user_id", "created_at"),
        # Частичный индекс для обхода активных порогов пары (HLD 6.2)
        Index("ix_alerts_active_pair", "pair_id", postgresql_where=text("active")),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE", name="fk_alerts_user_id_users"),
        nullable=False,
    )
    pair_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pairs.id", ondelete="CASCADE", name="fk_alerts_pair_id_pairs"),
        nullable=False,
    )
    condition: Mapped[str] = mapped_column(String(2), nullable=False)
    # Без precision/scale: NUMERIC остаётся точным (HLD 6.1, D03)
    threshold: Mapped[Decimal] = mapped_column(Numeric(), nullable=False)
    active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    # HLD 8: NULL = нужна исходная точка (F_Alert_23)
    last_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    last_quote_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trigger_seq: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    version: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("1")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class NotificationOutboxModel(Base):
    """Очередь доставки уведомлений в PostgreSQL (HLD 6.1, 8.1, D02).

    Уникальность (alert_id, trigger_seq) защищает от повторной регистрации
    одного пересечения порога. ON DELETE CASCADE удаляет события при удалении
    уведомления; дополнительно pending/retry/sending отменяются в репозитории.
    """

    __tablename__ = "notification_outbox"
    __table_args__ = (
        UniqueConstraint(
            "alert_id", "trigger_seq", name="uq_outbox_alert_trigger_seq"
        ),
        CheckConstraint(
            "status IN ('pending', 'sending', 'sent', 'retry', 'failed', 'cancelled', 'unknown')",
            name="ck_outbox_status",
        ),
        CheckConstraint("attempts >= 0", name="ck_outbox_attempts_non_negative"),
        # Частичный индекс для выборки due-событий (HLD 6.2)
        Index(
            "ix_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'retry')"),
        ),
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True)
    alert_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey(
            "alerts.id", ondelete="CASCADE", name="fk_outbox_alert_id_alerts"
        ),
        nullable=False,
    )
    trigger_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(12), nullable=False, server_default=text("'pending'")
    )
    attempts: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    lease_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    telegram_message_id: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    last_error_code: Mapped[str | None] = mapped_column(Text, nullable=True)