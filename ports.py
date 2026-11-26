"""Интерфейсы внешних зависимостей бота и фонового обработчика."""

from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Protocol
from uuid import UUID

from contracts import Alert, AssetPair, DialogSession, OutboxEvent, Quote, SeriesKind, TriggerEvent


class QuoteProvider(Protocol):
    async def get_batch(self, pairs: Sequence[AssetPair]) -> Sequence[Quote]: ...


class HistoryProvider(Protocol):
    async def get_series(
        self, pair: AssetPair, start: datetime, end: datetime
    ) -> Sequence[Quote]: ...


class QuoteRepository(Protocol):
    async def get_pair(self, pair_id: UUID) -> AssetPair | None: ...

    async def latest(self, pair_id: UUID, *, kind: SeriesKind) -> Quote | None: ...

    async def get_series(
        self, pair_id: UUID, start: datetime, end: datetime, *, kind: SeriesKind
    ) -> Sequence[Quote]: ...

    async def save(self, quotes: Sequence[Quote], *, kind: SeriesKind) -> None: ...


class AlertRepository(Protocol):
    async def list_for_user(self, user_id: UUID) -> Sequence[Alert]: ...

    async def get(self, user_id: UUID, alert_id: UUID) -> Alert | None: ...

    async def save(self, alert: Alert, *, expected_version: int | None) -> Alert:
        """Версия None означает создание. Устаревшая версия даёт ошибку Conflict."""
        ...

    async def delete(self, user_id: UUID, alert_id: UUID) -> None: ...

    async def list_active_for_pair(self, pair_id: UUID) -> Sequence[Alert]:
        """Активные уведомления пары для обхода в AlertChecker (HLD 8.1)."""
        ...

    async def lock_for_update(self, alert_id: UUID) -> Alert | None:
        """SELECT FOR UPDATE — блокирует запись на время перехода порога (HLD 6.3)."""
        ...


class OutboxRepository(Protocol):
    """Очередь доставки уведомлений в PostgreSQL (HLD 6.1, 8.1)."""

    async def enqueue(self, alert_id: UUID, trigger_seq: int, payload: dict) -> None:
        """Идемпотентная вставка: повторный (alert_id, trigger_seq) не создаёт дубль."""
        ...

    async def fetch_due(self, now: datetime, *, limit: int) -> Sequence[OutboxEvent]:
        """Выборка событий, готовных к отправке (pending/retry с next_attempt_at <= now)."""
        ...

    async def lease(self, event_id: UUID, *, until: datetime) -> bool:
        """Перевод pending/retry → sending. False, если событие уже занято или ушло."""
        ...

    async def mark_sent(
        self, event_id: UUID, *, telegram_message_id: int, sent_at: datetime
    ) -> None: ...

    async def mark_retry(
        self, event_id: UUID, *, next_attempt_at: datetime, error: str
    ) -> None: ...

    async def mark_failed(self, event_id: UUID, *, error: str) -> None: ...

    async def mark_unknown(self, event_id: UUID, *, error: str) -> None:
        """Обрыв после отправки: факт принятия Telegram неизвестен (HLD 8.1)."""
        ...

    async def cancel_for_alert(self, alert_id: UUID) -> None:
        """Отмена pending/retry/sending при удалении уведомления (HLD 6.3)."""
        ...


class SessionRepository(Protocol):
    async def get(self, user_id: UUID) -> DialogSession | None: ...

    async def save(
        self, session: DialogSession, *, expected_version: int | None
    ) -> DialogSession:
        """Версия None означает создание. Устаревшая версия даёт ошибку Conflict."""
        ...


class UnitOfWork(Protocol):
    """Репозитории в пределах одной транзакции. Без commit изменения откатываются."""

    @property
    def quotes(self) -> QuoteRepository: ...

    @property
    def alerts(self) -> AlertRepository: ...

    @property
    def sessions(self) -> SessionRepository: ...

    @property
    def outbox(self) -> OutboxRepository: ...

    async def commit(self) -> None: ...


type OpenUnitOfWork = Callable[[], AbstractAsyncContextManager[UnitOfWork]]


class Clock(Protocol):
    def now(self) -> datetime:
        """Текущее время UTC с часовым поясом."""
        ...


class Scheduler(Protocol):
    async def tick(self) -> None:
        """Возвращает управление при наступлении следующего тика."""
        ...


class IdGenerator(Protocol):
    def next(self) -> UUID: ...


class DeliveryFailed(Exception):
    """Адаптер установил, что сообщение не отправлено."""


class DeliveryUncertain(Exception):
    """У адаптера нет подтверждения, дошло ли сообщение."""


class Messenger(Protocol):
    async def send_alert(self, event: TriggerEvent) -> int:
        """Возвращает ID подтверждённого сообщения или поднимает DeliveryFailed / DeliveryUncertain."""
        ...