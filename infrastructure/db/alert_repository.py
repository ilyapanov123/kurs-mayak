"""Async PostgreSQL implementation of AlertRepository (DB-03)."""

from collections.abc import Sequence
from dataclasses import replace
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from contracts import Alert, AppError, ErrorCode
from infrastructure.db.models import AlertModel, NotificationOutboxModel


class PostgresAlertRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    @staticmethod
    def _to_contract(row: AlertModel) -> Alert:
        return Alert(
            id=row.id,
            user_id=row.user_id,
            pair_id=row.pair_id,
            condition=row.condition,
            threshold=row.threshold,
            active=row.active,
            last_match=row.last_match,
            last_quote_at=row.last_quote_at,
            trigger_seq=row.trigger_seq,
            version=row.version,
        )

    async def list_for_user(self, user_id: UUID) -> Sequence[Alert]:
        statement = (
            select(AlertModel)
            .where(AlertModel.user_id == user_id)
            .order_by(AlertModel.created_at)
        )
        rows = (await self._session.execute(statement)).scalars().all()
        return [self._to_contract(row) for row in rows]

    async def get(self, user_id: UUID, alert_id: UUID) -> Alert | None:
        row = await self._session.scalar(
            select(AlertModel)
            .where(AlertModel.id == alert_id, AlertModel.user_id == user_id)
        )
        return self._to_contract(row) if row is not None else None

    async def save(self, alert: Alert, *, expected_version: int | None) -> Alert:
        if alert.last_quote_at is not None and alert.last_quote_at.utcoffset() is None:
            raise ValueError("last_quote_at должен содержать часовой пояс")

        if expected_version is None:
            statement = select(AlertModel).where(AlertModel.id == alert.id)
            if await self._session.scalar(statement) is not None:
                raise AppError(
                    ErrorCode.CONFLICT,
                    "Уведомление уже существует.",
                    retryable=False,
                    correlation_id=alert.id,
                )
            self._session.add(
                AlertModel(
                    id=alert.id,
                    user_id=alert.user_id,
                    pair_id=alert.pair_id,
                    condition=alert.condition,
                    threshold=alert.threshold,
                    active=alert.active,
                    last_match=alert.last_match,
                    last_quote_at=alert.last_quote_at,
                    trigger_seq=alert.trigger_seq,
                    version=1,
                )
            )
            await self._session.flush()
            return replace(alert, version=1)

        statement = (
            update(AlertModel)
            .where(
                AlertModel.id == alert.id,
                AlertModel.user_id == alert.user_id,
                AlertModel.version == expected_version,
            )
            .values(
                condition=alert.condition,
                threshold=alert.threshold,
                active=alert.active,
                last_match=alert.last_match,
                last_quote_at=alert.last_quote_at,
                trigger_seq=alert.trigger_seq,
                version=AlertModel.version + 1,
                updated_at=func.now(),
            )
            .returning(AlertModel.version)
        )
        version = await self._session.scalar(statement)
        if version is None:
            raise AppError(
                ErrorCode.CONFLICT,
                "Кнопка устарела.",
                retryable=False,
                correlation_id=alert.id,
            )
        return replace(alert, version=version)

    async def delete(self, user_id: UUID, alert_id: UUID) -> None:
        exists = await self._session.scalar(
            select(AlertModel.id)
            .where(AlertModel.id == alert_id, AlertModel.user_id == user_id)
        )
        if exists is None:
            raise AppError(
                ErrorCode.NOT_FOUND,
                "Уведомление не найдено.",
                retryable=False,
                correlation_id=alert_id,
            )

        await self._session.execute(
            update(NotificationOutboxModel)
            .where(
                NotificationOutboxModel.alert_id == alert_id,
                NotificationOutboxModel.status.in_(("pending", "retry", "sending")),
            )
            .values(status="cancelled")
        )

        await self._session.execute(
            delete(AlertModel).where(AlertModel.id == alert_id)
        )
        await self._session.flush()

    async def list_active_for_pair(self, pair_id: UUID) -> Sequence[Alert]:
        rows = (
            await self._session.scalars(
                select(AlertModel)
                .where(AlertModel.pair_id == pair_id, AlertModel.active.is_(True))
            )
        ).all()
        return [self._to_contract(row) for row in rows]

    async def lock_for_update(self, alert_id: UUID) -> Alert | None:
        row = await self._session.scalar(
            select(AlertModel)
            .where(AlertModel.id == alert_id)
            .with_for_update()
        )
        return self._to_contract(row) if row is not None else None
