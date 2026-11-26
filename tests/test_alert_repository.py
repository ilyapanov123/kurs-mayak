"""Модульные тесты AlertRepository ( DB-02)."""
from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from sqlalchemy.dialects import postgresql

from contracts import Alert, AppError, ErrorCode
from infrastructure.db.alert_repository import PostgresAlertRepository
from infrastructure.db.models import AlertModel

USER_ID = UUID("00000000-0000-0000-0000-000000000001")
PAIR_ID = UUID("00000000-0000-0000-0000-000000000002")
ALERT_ID = UUID("00000000-0000-0000-0000-000000000003")
T0 = datetime(2026, 11, 26, 12, tzinfo=UTC)


def make_alert(**overrides) -> Alert:
    base = dict(
        id=ALERT_ID, user_id=USER_ID, pair_id=PAIR_ID,
        condition="ge", threshold=Decimal("100"),
        active=True, last_match=None, last_quote_at=None,
        trigger_seq=0, version=0,
    )
    base.update(overrides)
    return Alert(**base)


@pytest.mark.asyncio
async def test_get_filters_by_user_id_and_alert_id() -> None:
    """NF_Sec_1-2: доступ всегда по id И user_id."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    repo = PostgresAlertRepository(session)
    assert await repo.get(USER_ID, ALERT_ID) is None
    sql = str(session.scalar.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "user_id" in sql and "id" in sql


@pytest.mark.asyncio
async def test_save_new_inserts_with_version_one() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)  # не существует
    repo = PostgresAlertRepository(session)
    saved = await repo.save(make_alert(), expected_version=None)
    assert saved.version == 1
    session.add.assert_called_once()
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_existing_uses_optimistic_lock() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=5)
    repo = PostgresAlertRepository(session)
    saved = await repo.save(make_alert(version=4), expected_version=4)
    assert saved.version == 5
    sql = str(session.scalar.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "WHERE alerts.id" in sql and "alerts.version" in sql


@pytest.mark.asyncio
async def test_save_stale_version_raises_conflict() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)  # UPDATE не нашёл строку
    repo = PostgresAlertRepository(session)
    with pytest.raises(AppError) as exc:
        await repo.save(make_alert(version=99), expected_version=99)
    assert exc.value.code == ErrorCode.CONFLICT


@pytest.mark.asyncio
async def test_delete_cancels_pending_then_deletes() -> None:
    """HLD 6.3: судьба очереди при удалении."""
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=ALERT_ID)
    session.execute = AsyncMock()
    repo = PostgresAlertRepository(session)
    await repo.delete(USER_ID, ALERT_ID)
    assert session.execute.await_count == 2


@pytest.mark.asyncio
async def test_lock_for_update_uses_with_for_update() -> None:
    session = AsyncMock()
    session.scalar = AsyncMock(return_value=None)
    repo = PostgresAlertRepository(session)
    await repo.lock_for_update(ALERT_ID)
    sql = str(session.scalar.await_args.args[0].compile(dialect=postgresql.dialect()))
    assert "FOR UPDATE" in sql


def test_alert_model_columns_match_hld_6_1() -> None:
    table = AlertModel.__table__
    assert table.c.threshold.type.__class__.__name__ == "Numeric"
    assert table.c.threshold.type.precision is None
    assert table.c.last_quote_at.type.timezone is True
    assert table.c.trigger_seq.type.__class__.__name__ == "BigInteger"
    assert table.c.version.type.__class__.__name__ == "BigInteger"
    constraint_names = {c.name for c in table.constraints}
    assert "ck_alerts_condition" in constraint_names
    assert "ck_alerts_threshold_positive" in constraint_names