from aiogram import Router, BaseMiddleware
from aiogram.types import Message, TelegramObject
from typing import Dict, Any, Callable, Awaitable, Optional, List, Set
import re

router = Router()

# Конфигурируемые настройки
admin_user_id: Optional[int] = None
forbidden_words: Set[str] = set()
forbidden_link_patterns: List[str] = []
allowed_commands: Set[str] = {
    '/start', '/help', '/ping', '/latest', '/news', '/filter', '/source', 
    '/sources', '/stats', '/arc_filter', '/src_stat', '/arc_months', 
    '/archive_now', '/fetch'
}


def setup_content_filter_config(admin_id: Optional[int], bad_words: List[str] = None, 
                               bad_links: List[str] = None, commands: List[str] = None):
    """Настройка конфигурации фильтра контента"""
    global admin_user_id, forbidden_words, forbidden_link_patterns, allowed_commands
    admin_user_id = admin_id
    
    if bad_words:
        forbidden_words = {word.lower() for word in bad_words}
    
    if bad_links:
        forbidden_link_patterns = bad_links
    
    if commands:
        allowed_commands = set(commands)


class ContentFilterMiddleware(BaseMiddleware):
    def __init__(self):
        pass

    def is_admin(self, user_id: int) -> bool:
        """Проверка, является ли пользователь админом"""
        return admin_user_id is not None and user_id == admin_user_id

    def is_command(self, text: str) -> bool:
        """Проверка, является ли сообщение командой"""
        return text.startswith('/')

    def extract_command(self, text: str) -> str:
        """Извлечение команды из текста"""
        if not text.startswith('/'):
            return ''
        
        # Извлекаем первое слово после /
        parts = text.split()
        if not parts:
            return ''
        
        command = parts[0]
        # Убираем @bot_username если есть
        if '@' in command:
            command = command.split('@')[0]
        
        return command

    def check_forbidden_words(self, text: str) -> List[str]:
        """Проверка на запрещенные слова"""
        if not forbidden_words:
            return []
        
        text_lower = text.lower()
        found_words = []
        
        for word in forbidden_words:
            if word in text_lower:
                found_words.append(word)
        
        return found_words

    def check_forbidden_links(self, text: str) -> List[str]:
        """Проверка на запрещенные ссылки"""
        if not forbidden_link_patterns:
            return []
        
        found_patterns = []
        
        for pattern in forbidden_link_patterns:
            try:
                if re.search(pattern, text, re.IGNORECASE):
                    found_patterns.append(pattern)
            except re.error:
                # Если паттерн некорректный, проверяем как простую подстроку
                if pattern.lower() in text.lower():
                    found_patterns.append(pattern)
        
        return found_patterns

    def check_command_validity(self, text: str) -> tuple[bool, str]:
        """Проверка валидности команды"""
        if not self.is_command(text):
            return True, ''  # Не команда - пропускаем
        
        command = self.extract_command(text)
        if not command:
            return False, 'Некорректная команда'
        
        if command not in allowed_commands:
            return False, f'Неизвестная команда: {command}'
        
        return True, ''

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        if not isinstance(event, Message) or not event.from_user or not event.text:
            return await handler(event, data)

        user_id = event.from_user.id
        text = event.text

        # Админ-байпас
        if self.is_admin(user_id):
            return await handler(event, data)

        # Проверка команд
        if self.is_command(text):
            is_valid, error_msg = self.check_command_validity(text)
            if not is_valid:
                await event.answer(f"❌ {error_msg}\n\nИспользуйте /help для просмотра доступных команд.")
                return  # Блокируем некорректную команду
            
            # Команды проходят без дальнейших проверок
            return await handler(event, data)

        # Проверка на запрещенные слова (только для не-команд)
        forbidden_found = self.check_forbidden_words(text)
        if forbidden_found:
            await event.answer(
                f"🚫 Сообщение содержит запрещенные слова: {', '.join(forbidden_found)}\n"
                "Ваше сообщение заблокировано."
            )
            return  # Блокируем сообщение

        # Проверка на запрещенные ссылки (только для не-команд)
        forbidden_links = self.check_forbidden_links(text)
        if forbidden_links:
            await event.answer(
                "🚫 Сообщение содержит запрещенные ссылки или паттерны.\n"
                "Ваше сообщение заблокировано."
            )
            return  # Блокируем сообщение

        return await handler(event, data)


# Экземпляр middleware для использования в main.py
content_filter_middleware = ContentFilterMiddleware()