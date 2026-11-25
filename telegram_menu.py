from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from uuid import UUID

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from contracts import DialogSession
from ports import SessionRepository


type OpenDialog = Callable[
    [int, int, bool], AbstractAsyncContextManager[tuple[UUID | None, SessionRepository]]
]

MENU = "idle"
SECTIONS = {
    "rate": "Курс валют",
    "crypto": "Криптовалюта",
    "dyn": "Динамика",
    "alerts": "Уведомления",
}
BACK = "n:back"
HOME = "n:home"
STALE_BUTTON = "Кнопка устарела. Используйте последнее меню или отправьте /start."


def screen(session: DialogSession) -> tuple[str, InlineKeyboardMarkup]:
    if session.state == MENU:
        title = "Главное меню"
        buttons = [(label, f"m:{section}") for section, label in SECTIONS.items()]
    else:
        title = SECTIONS[session.state]
        buttons = [("Назад", BACK), ("Главное меню", HOME)]

    return title, InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"{route}:{session.version}")]
            for label, route in buttons
        ]
    )


def create_menu_router(open_dialog: OpenDialog) -> Router:
    router = Router(name="telegram_menu")
    router.message.filter(F.chat.type == ChatType.PRIVATE)

    @router.message(CommandStart())
    @router.message(Command("cancel"))
    async def reset_dialog(message: Message) -> None:
        if message.from_user is None:
            return
        async with open_dialog(message.from_user.id, message.chat.id, True) as (user_id, sessions):
            assert user_id is not None
            current = await sessions.get(user_id)
            version = current.version if current else None
            saved = await sessions.save(
                DialogSession(user_id, MENU, {}, version or 0), expected_version=version
            )
        title, keyboard = screen(saved)
        await message.answer(title, reply_markup=keyboard)

    @router.callback_query(F.data.regexp(r"^(?:m|n):"))
    async def navigate(query: CallbackQuery) -> None:
        message = query.message
        if not isinstance(message, Message) or message.chat.type != ChatType.PRIVATE:
            await query.answer(STALE_BUTTON)
            return

        route, _, button_version = (query.data or "").rpartition(":")
        saved = None
        async with open_dialog(query.from_user.id, message.chat.id, False) as (user_id, sessions):
            current = await sessions.get(user_id) if user_id is not None else None
            if current is not None and button_version == str(current.version):
                next_state = None
                if current.state == MENU and route.startswith("m:") and route[2:] in SECTIONS:
                    next_state = route[2:]
                elif current.state in SECTIONS and route in (BACK, HOME):
                    next_state = MENU
                if next_state is not None:
                    saved = await sessions.save(
                        DialogSession(current.user_id, next_state, {}, current.version),
                        expected_version=current.version,
                    )

        if saved is None:
            await query.answer(STALE_BUTTON)
            return

        title, keyboard = screen(saved)
        await query.answer()
        await message.edit_text(title, reply_markup=keyboard)

    @router.message(F.text.startswith("/"))
    async def unknown_command(message: Message) -> None:
        await message.answer("Команда не распознана. Используйте /start или /cancel.")

    @router.message()
    async def invalid_input(message: Message) -> None:
        await message.answer("Используйте кнопки текущего меню или отправьте /start.")

    return router
