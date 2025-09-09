from aiogram import Router
from aiogram.types import Message
from aiogram.filters import Command
from typing import Optional

router = Router()

# Конфигурируемые настройки админа
admin_user_id: Optional[int] = None


def setup_admin(user_id: Optional[int]):
    """Настройка админ-аккаунта"""
    global admin_user_id
    admin_user_id = user_id


def is_admin(user_id: int) -> bool:
    """Проверка, является ли пользователь админом"""
    return admin_user_id is not None and user_id == admin_user_id


@router.message(Command("start"))
async def start_cmd(message: Message):
    """Обработчик команды /start - доступна только для админа"""
    if not message.from_user:
        await message.answer("Не удается определить пользователя.")
        return
    
    if not is_admin(message.from_user.id):
        await message.answer("Доступ запрещен. Команда доступна только для администратора.")
        return
    
    start_text = (
        "🤖 Telegram News Aggregator Bot\n\n"
        "Добро пожаловать, администратор!\n\n"
        "Этот бот агрегирует новости из RSS-лент независимых медиа.\n"
        "Доступен полнотекстовый поиск и управление источниками.\n\n"
        "Используйте /help для получения полного списка команд."
    )
    await message.answer(start_text)


@router.message(Command("ping"))
async def ping_cmd(message: Message):
    """Обработчик команды /ping - проверка работы бота"""
    await message.answer("🏓 Pong! Бот работает нормально.")