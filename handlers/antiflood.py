from aiogram import Router, BaseMiddleware
from aiogram.types import Message, TelegramObject
from typing import Dict, Any, Callable, Awaitable, Optional
import time
from collections import defaultdict

router = Router()

# Конфигурируемые настройки
admin_user_id: Optional[int] = None
message_limit: int = 5  # Лимит сообщений
time_window: int = 60   # Временное окно в секундах
warning_threshold: int = 3  # Количество предупреждений до мута
mute_duration: int = 300  # Длительность мута в секундах


def setup_antiflood_config(admin_id: Optional[int], msg_limit: int = 5, time_win: int = 60, 
                          warn_threshold: int = 3, mute_dur: int = 300):
    """Настройка конфигурации антифлуда"""
    global admin_user_id, message_limit, time_window, warning_threshold, mute_duration
    admin_user_id = admin_id
    message_limit = msg_limit
    time_window = time_win
    warning_threshold = warn_threshold
    mute_duration = mute_dur


class AntiFloodMiddleware(BaseMiddleware):
    def __init__(self):
        # Словари для отслеживания активности пользователей
        self.user_messages: Dict[int, list] = defaultdict(list)
        self.user_warnings: Dict[int, int] = defaultdict(int)
        self.muted_until: Dict[int, float] = defaultdict(float)
        self.warned_users: set = set()  # Пользователи, которые уже получили предупреждение в текущем цикле

    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь админом"""
        return admin_user_id is not None and user_id == admin_user_id

    def is_command(self, text: str) -> bool:
        """Проверка, является ли сообщение командой"""
        return text.startswith('/')

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user:
            return await handler(event, data)

        user_id = event.from_user.id
        current_time = time.time()

        # Админ-байпас
        if self.is_admin(user_id):
            return await handler(event, data)

        # Командные сообщения не подвергаются антифлуду
        if event.text and self.is_command(event.text):
            return await handler(event, data)

        # Проверка, не находится ли пользователь в муте
        if user_id in self.muted_until and current_time < self.muted_until[user_id]:
            remaining_time = int(self.muted_until[user_id] - current_time)
            if user_id not in self.warned_users:
                await event.answer(f"🔇 Вы находитесь в муте. Осталось: {remaining_time} сек.")
                self.warned_users.add(user_id)
            return  # Блокируем сообщение

        # Очистка истекших мутов
        if user_id in self.muted_until and current_time >= self.muted_until[user_id]:
            del self.muted_until[user_id]
            self.warned_users.discard(user_id)

        # Очистка старых сообщений из временного окна
        messages = self.user_messages[user_id]
        messages[:] = [msg_time for msg_time in messages if current_time - msg_time <= time_window]

        # Добавление текущего сообщения
        messages.append(current_time)

        # Проверка лимита сообщений
        if len(messages) > message_limit:
            warnings = self.user_warnings[user_id]
            
            if warnings < warning_threshold:
                # Увеличиваем количество предупреждений
                self.user_warnings[user_id] += 1
                remaining_warnings = warning_threshold - self.user_warnings[user_id]
                
                if remaining_warnings > 0:
                    await event.answer(
                        f"⚠️ Предупреждение {self.user_warnings[user_id]}/{warning_threshold}: "
                        f"Слишком много сообщений! Лимит: {message_limit} сообщений за {time_window} сек.\n"
                        f"Осталось предупреждений до мута: {remaining_warnings}"
                    )
                else:
                    # Мут пользователя
                    self.muted_until[user_id] = current_time + mute_duration
                    self.warned_users.add(user_id)
                    await event.answer(
                        f"🔇 Вы превысили лимит сообщений и получили мут на {mute_duration} секунд.\n"
                        f"После снятия мута счетчик предупреждений сбросится."
                    )
                    # Сброс предупреждений после мута (не сразу, а когда мут закончится)
                return  # Блокируем сообщение
            else:
                # Если пользователь уже достиг лимита предупреждений, просто заблокировать
                if user_id not in self.warned_users:
                    await event.answer("🔇 Сообщение заблокировано из-за превышения лимита.")
                    self.warned_users.add(user_id)
                return  # Блокируем сообщение

        # Сброс предупреждений, если пользователь не превышал лимит в течение временного окна
        if len(messages) <= message_limit and user_id in self.user_warnings:
            # Проверяем, прошло ли достаточно времени с последнего превышения
            last_violation_time = max(messages) if messages else 0
            if current_time - last_violation_time > time_window:
                del self.user_warnings[user_id]
                self.warned_users.discard(user_id)

        return await handler(event, data)


# Экземпляр middleware для использования в main.py
antiflood_middleware = AntiFloodMiddleware()