from uuid import UUID, uuid4

from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from contracts import AppError, DialogSession, ErrorCode
from infrastructure.db.models import DialogSessionModel, UserModel


class PostgresSessionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_user_id(self, telegram_user_id: int, chat_id: int, *, create: bool) -> UUID | None:
        if create:
            statement = insert(UserModel).values(
                id=uuid4(), telegram_user_id=telegram_user_id, chat_id=chat_id
            )
            return await self._session.scalar(
                statement.on_conflict_do_update(
                    index_elements=[UserModel.telegram_user_id],
                    set_={"chat_id": statement.excluded.chat_id},
                ).returning(UserModel.id)
            )

        # Команды и кнопки одного пользователя меняют диалог по очереди.
        return await self._session.scalar(
            select(UserModel.id)
            .where(UserModel.telegram_user_id == telegram_user_id, UserModel.chat_id == chat_id)
            .with_for_update()
        )

    async def get(self, user_id: UUID) -> DialogSession | None:
        row = await self._session.get(DialogSessionModel, user_id)
        if row is None:
            return None
        return DialogSession(user_id, row.state, row.payload, row.version)

    async def save(
        self, session: DialogSession, *, expected_version: int | None
    ) -> DialogSession:
        values = {"state": session.state, "payload": session.payload}
        if expected_version is None:
            statement = (
                insert(DialogSessionModel)
                .values(user_id=session.user_id, version=1, **values)
                .on_conflict_do_nothing(index_elements=[DialogSessionModel.user_id])
            )
        else:
            statement = (
                update(DialogSessionModel)
                .where(
                    DialogSessionModel.user_id == session.user_id,
                    DialogSessionModel.version == expected_version,
                )
                .values(version=DialogSessionModel.version + 1, updated_at=func.now(), **values)
            )

        version = await self._session.scalar(statement.returning(DialogSessionModel.version))
        if version is None:
            raise AppError(
                ErrorCode.CONFLICT, "Кнопка устарела.", retryable=False, correlation_id=uuid4()
            )
        return DialogSession(session.user_id, session.state, session.payload, version)
