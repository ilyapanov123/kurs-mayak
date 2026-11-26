
"""Модульные тесты OutboxRepository (стиль DB-02)."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from infrastructure.db.models import NotificationOutboxModel
from infrastructure.db.outbox_repository import PostgresOutboxRepository

ALERT_ID = UUID("00000000-0000-0000-0000-000000000003")
EVENT_ID = UUID("00000000-0000-0000-0000-000000000004")
T0 = datetime(2026, 11, 26, 12, tzinfo=UTC)


@pytest.mark.asyncio
async def test_enqueue_uses_on_conflict_do_nothing() -> None:
    """Критерий приёмки DB-03: уникальность (alert_id, trigger_seq) на стороне БД."""
    session = AsyncMock()
    repo = PostgresOutboxRepository(session)

    await repo.enqueue(ALERT_ID, 1, {"price": "100"})

    sql = str(
        session.execute.await_args.args[0].compile(
            dialect=postgresql.dialect()
        )
    )

    assert "ON CONFLICT" in sql and "DO NOTHING" in sql
    assert "alert_id" in sql and "trigger_seq" in sql


@pytest.mark.asyncio
async def test_fetch_due_filters_status_and_time() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = []
    session.scalars.return_value = result

    repo = PostgresOutboxRepository(session)
    await repo.fetch_due(T0, limit=10)

    compiled = session.scalars.await_args.args[0].compile(
        dialect=postgresql.dialect()
    )

    sql = str(compiled)
    params = repr(compiled.params)

    assert "status IN" in sql
    assert "pending" in params
    assert "retry" in params
    assert "next_attempt_at" in sql


@pytest.mark.asyncio
async def test_lease_transitions_to_sending() -> None:
    session = AsyncMock()
    result = MagicMock()
    result.rowcount = 1
    session.execute.return_value = result

    repo = PostgresOutboxRepository(session)

    assert await repo.lease(EVENT_ID, until=T0) is True

    compiled = session.execute.await_args.args[0].compile(
        dialect=postgresql.dialect()
    )

    sql = str(compiled)
    params = repr(compiled.params)

    assert "UPDATE notification_outbox" in sql
    assert "sending" in params
    assert "lease_until" in sql


def test_outbox_model_has_unique_constraint() -> None:
    """Критерий приёмки DB-03: UNIQUE(alert_id, trigger_seq) на уровне БД."""
    table = NotificationOutboxModel.__table__

    unique_names = {
        c.name
        for c in table.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }

    assert "uq_outbox_alert_trigger_seq" in unique_names

    fk_names = {
        c.name
        for c in table.constraints
        if c.__class__.__name__ == "ForeignKeyConstraint"
    }

    assert "fk_outbox_alert_id_alerts" in fk_names

    for c in table.constraints:
        if (
            c.__class__.__name__ == "ForeignKeyConstraint"
            and c.name == "fk_outbox_alert_id_alerts"
        ):
            assert c.ondelete == "CASCADE"
