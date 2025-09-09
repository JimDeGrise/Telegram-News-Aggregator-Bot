#!/usr/bin/env python3
"""
Простой тест для проверки основной функциональности новых хендлеров.
Этот скрипт проверяет импорты и базовую конфигурацию.
"""

import os
import sys

# Добавляем текущую директорию в PYTHONPATH
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def test_imports():
    """Тест импортов всех модулей"""
    try:
        import main
        import handlers.help
        import handlers.commands
        import handlers.antiflood
        import handlers.content_filter
        print("✓ Все модули успешно импортированы")
        return True
    except Exception as e:
        print(f"✗ Ошибка импорта: {e}")
        return False

def test_handler_configuration():
    """Тест конфигурации хендлеров"""
    try:
        from handlers.commands import setup_admin, is_admin
        from handlers.antiflood import setup_antiflood_config
        from handlers.content_filter import setup_content_filter_config
        
        # Тест настройки админа
        setup_admin(12345)
        assert is_admin(12345) == True
        assert is_admin(54321) == False
        print("✓ Настройка админа работает корректно")
        
        # Тест настройки антифлуда
        setup_antiflood_config(
            admin_id=12345,
            msg_limit=3,
            time_win=30,
            warn_threshold=2,
            mute_dur=120
        )
        print("✓ Настройка антифлуда работает корректно")
        
        # Тест настройки фильтра контента
        setup_content_filter_config(
            admin_id=12345,
            bad_words=['тест', 'спам'],
            bad_links=['example\\.com', 'badsite']
        )
        print("✓ Настройка фильтра контента работает корректно")
        
        return True
    except Exception as e:
        print(f"✗ Ошибка конфигурации: {e}")
        return False

def test_middleware_logic():
    """Тест логики middleware"""
    try:
        from handlers.antiflood import AntiFloodMiddleware
        from handlers.content_filter import ContentFilterMiddleware
        
        # Создаем экземпляры
        antiflood = AntiFloodMiddleware()
        content_filter = ContentFilterMiddleware()
        
        # Тест проверки команд
        assert antiflood.is_command('/start') == True
        assert antiflood.is_command('привет') == False
        print("✓ Проверка команд в антифлуде работает")
        
        assert content_filter.is_command('/help') == True
        assert content_filter.is_command('обычное сообщение') == False
        print("✓ Проверка команд в фильтре работает")
        
        # Тест извлечения команды
        assert content_filter.extract_command('/start') == '/start'
        assert content_filter.extract_command('/help@botname') == '/help'
        assert content_filter.extract_command('/ping arg1 arg2') == '/ping'
        print("✓ Извлечение команд работает корректно")
        
        return True
    except Exception as e:
        print(f"✗ Ошибка логики middleware: {e}")
        return False

def main():
    """Главная функция тестирования"""
    print("Запуск тестов для новых хендлеров...")
    print("=" * 50)
    
    tests = [
        test_imports,
        test_handler_configuration,
        test_middleware_logic,
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        try:
            if test():
                passed += 1
            print()
        except Exception as e:
            print(f"✗ Неожиданная ошибка в {test.__name__}: {e}")
            print()
    
    print("=" * 50)
    print(f"Результат: {passed}/{total} тестов прошли успешно")
    
    if passed == total:
        print("🎉 Все тесты пройдены!")
        return 0
    else:
        print("⚠️ Некоторые тесты не прошли")
        return 1

if __name__ == "__main__":
    exit(main())