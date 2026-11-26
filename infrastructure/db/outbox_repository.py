"""Async PostgreSQL implementation of OutboxRepository (DB-03)."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from contracts import OutboxEvent
from infrastructure.db.models import NotificationOutboxModel


class PostgresOutboxRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_contract(row: NotificationOutboxModel) -> OutboxEvent:
        return OutboxEvent(
            id=row.id,
            alert_id=row.alert_id,
            trigger_seq=row.trigger_seq,
            payload=row.payload,
            status=row.status,  # type: ignore[arg-type]
            attempts=row.attempts,
            next_attempt_at=row.next_attempt_at,
            lease_until=row.lease_until,
            sent_at=row.sent_at,
            telegram_message_id=row.telegram_message_id,
            last_error_code=row.last_error_code,
        )

    async def enqueue(
        self, alert_id: UUID, trigger_seq: int, payload: dict
    ) -> None:
        """Идемпотентная вставка: UNIQUE(alert_id, trigger_seq) (HLD 6.1)."""
        statement = insert(NotificationOutboxModel).values(
            id=uuid4(),
            alert_id=alert_id,
            trigger_seq=trigger_seq,
            payload=payload,
            status="pending",
            attempts=0,
            next_attempt_at=datetime.now(),
        )
        await self._session.execute(
            statement.on_conflict_do_nothing(
                index_elements=[
                    NotificationOutboxModel.alert_id,
                    NotificationOutboxModel.trigger_seq,
                ]
            )
        )
        await self._session.flush()

    async def fetch_due(self, now: datetime, *, limit: int) -> Sequence[OutboxEvent]:
        rows = (
            await self._session.scalars(
                select(NotificationOutboxModel)
                .where(
                    NotificationOutboxModel.status.in_(("pending", "retry")),
                    NotificationOutboxModel.next_attempt_at <= now,
                )
                .order_by(NotificationOutboxModel.next_attempt_at)
                .limit(limit)
            )
        ).all()
        return [self._to_contract(row) for row in rows]

    async def lease(self, event_id: UUID, *, until: datetime) -> bool:
        result = await self._session.execute(
            update(NotificationOutboxModel)
            .where(
                NotificationOutboxModel.id == event_id,
                NotificationOutboxModel.status.in_(("pending", "retry")),
            )
            .values(status="sending", lease_until=until)
        )
        await self._session.flush()
        return result.rowcount == 1

    async def mark_sent(
        self, event_id: UUID, *, telegram_message_id: int, sent_at: datetime
    ) -> None:
        await self._session.execute(
            update(NotificationOutboxModel)
            .where(NotificationOutboxModel.id == event_id)
            .values(
                status="sent",
                sent_at=sent_at,
                telegram_message_id=telegram_message_id,
                lease_until=None,
                last_error_code=None,
            )
        )
        await self._session.flush()

    async def mark_retry(
        self, event_id: UUID, *, next_attempt_at: datetime, error: str
    ) -> None:
        await self._session.execute(
            update(NotificationOutboxModel)
            .where(NotificationOutboxModel.id == event_id)
            .values(
                status="retry",
                attempts=NotificationOutboxModel.attempts + 1,
                next_attempt_at=next_attempt_at,
                lease_until=None,
                last_error_code=error,
            )
        )
        await self._session.flush()

    async def mark_failed(self, event_id: UUID, *, error: str) -> None:
        await self._session.execute(
            update(NotificationOutboxModel)
            .where(NotificationOutboxModel.id == event_id)
            .values(status="failed", lease_until=None, last_error_code=error)
        )
        await self._session.flush()

    async def mark_unknown(self, event_id: UUID, *, error: str) -> None:
        # HLD 8.1/10.2: обрыв после отправки — повтор вслепую запрещён
        await self._session.execute(
            update(NotificationOutboxModel)
            .where(NotificationOutboxModel.id == event_id)
            .values(status="unknown", lease_until=None, last_error_code=error)
        )
        await self._session.flush()

    async def cancel_for_alert(self, alert_id: UUID) -> None:
        await self._session.execute(
            update(NotificationOutboxModel)
            .where(
                NotificationOutboxModel.alert_id == alert_id,
                NotificationOutboxModel.status.in_(("pending", "retry", "sending")),
            )
            .values(status="cancelled")
        )
        await self._session.flush()