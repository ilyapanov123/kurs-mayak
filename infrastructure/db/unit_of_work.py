"""PostgreSQL UnitOfWork: одна сессия = одна транзакция (HLD 1.1, 6.3)."""

from collections.abc import Callable
from types import TracebackType

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.db.alert_repository import PostgresAlertRepository
from infrastructure.db.outbox_repository import PostgresOutboxRepository
from infrastructure.db.quote_repository import PostgresQuoteRepository
from infrastructure.db.session_repository import PostgresSessionRepository
from ports import UnitOfWork


class PostgresUnitOfWork(UnitOfWork):
    """Внутри контекста все репозитории работают в одной транзакции.

    commit() фиксирует переход порога И событие outbox атомарно;
    любое исключение до commit() откатывает и то, и другое (критерий приёмки DB-03).
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> "PostgresUnitOfWork":
        self._session = self._session_factory()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        assert self._session is not None
        try:
            if exc_type is None:
                await self._session.commit()
            else:
                await self._session.rollback()
        finally:
            await self._session.close()
            self._session = None

    @property
    def _s(self) -> AsyncSession:
        assert self._session is not None
        return self._session

    @property
    def quotes(self) -> PostgresQuoteRepository:
        return PostgresQuoteRepository(self._s)

    @property
    def alerts(self) -> PostgresAlertRepository:
        return PostgresAlertRepository(self._s)

    @property
    def sessions(self) -> PostgresSessionRepository:
        return PostgresSessionRepository(self._s)

    @property
    def outbox(self) -> PostgresOutboxRepository:
        return PostgresOutboxRepository(self._s)

    async def commit(self) -> None:
        await self._s.commit()


def build_open_uow(
    session_factory: async_sessionmaker[AsyncSession],
) -> Callable[[], PostgresUnitOfWork]:
    """Фабрика для Adapters.open_uow (HLD 2, 4.2)."""
    return lambda: PostgresUnitOfWork(session_factory)