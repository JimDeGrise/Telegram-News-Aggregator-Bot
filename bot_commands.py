from aiogram import Router
from aiogram.types import (
    Message,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    CallbackQuery,
)
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from typing import Callable, Dict, List, Set, Optional
import hashlib
import html
import re
import inspect

try:
    from search_parser import parse_user_query, split_positive_negative
except ImportError:
    parse_user_query = None
    split_positive_negative = None

router = Router()

HELP_TEXT = (
    "Бот агрегирует RSS независимых медиа, доступен полнотекстовый поиск (FTS5: AND OR NOT, знак минус -, фразы в \"\").\n\n"
    "Команды:\n"
    "/start — краткая справка\n"
    "/help — эта справка\n"
    "/latest — последние N (фиксированно) новостей списком с пагинацией\n"
    "/news [N] — показать одну новость (по умолчанию 1-я). N — порядковый номер (1-based)\n"
    "/filter &lt;запрос&gt; |p — поиск (p — страница). Пример: /filter кризис AND экономика |2\n"
    "/source &lt;источник&gt; [N] — показать одну новость из источника (по умолчанию 1-я). Пример: /source meduza 5\n"
    "/sources — список источников с количеством\n"
    "/stats — текущая статистика по источникам (основная БД)\n"
    "/arc_filter [source] [YYYY-MM] [limit] — просмотр архива. Без аргументов покажет подсказку и доступные месяцы.\n"
    "    Примеры: /arc_filter 2025-08 5 | /arc_filter reuters 2025-07 | /arc_filter reuters 5\n"
    "/src_stat — статистика по архиву (по месяцам и общий топ источников)\n"
    "/arc_months — список доступных архивных месяцев\n"
    "/archive_now — запуск ручной архивации (админ)\n"
#   "/fetch — принудительный сбор (админ)\n\n"
    "Навигация:\n"
    "- В списках: « Пред / След » / Закрыть\n"
    "- В одиночной новости: ⏮ Перв. / « Пред / След » / Посл. ⏭ + ✖ Закрыть\n"
    "Особенности поиска: скобки ( ) не поддерживаются (FTS5). Фразы — в кавычках. Минус перед словом — исключение."
)

class LatestPage(CallbackData, prefix="lp"):
    offset: int
    limit: int

class FilterPage(CallbackData, prefix="fs"):
    key: str
    offset: int
    limit: int

class NewsItem(CallbackData, prefix="ni"):
    idx: int

class SourcePage(CallbackData, prefix="sp"):
    key: str
    offset: int
    limit: int

class SourceNewsItem(CallbackData, prefix="sni"):
    key: str
    idx: int

TAG_RE = re.compile(r"<[^>]+>")
BRACKET_ENTITY_RE = re.compile(r"\[&#\d+;?\]")
MULTISPACE_RE = re.compile(r"[ \t\r\f\v]+")
NEWLINE_RE = re.compile(r"\n{3,}")
NBSP_RE = re.compile(r"\u00A0")

def clean_text(raw: str) -> str:
    if not raw:
        return ""
    txt = html.unescape(raw)
    txt = TAG_RE.sub("", txt)
    txt = BRACKET_ENTITY_RE.sub("…", txt)
    txt = NBSP_RE.sub(" ", txt)
    txt = MULTISPACE_RE.sub(" ", txt)
    txt = NEWLINE_RE.sub("\n\n", txt)
    txt = txt.strip()
    return html.escape(txt)

def safe_join(parts):
    return "\n\n".join(p for p in parts if p)

def build_highlight_patterns(raw_query: str) -> List[str]:
    patterns: Set[str] = set()
    if not parse_user_query or not split_positive_negative:
        return []
    try:
        ast = parse_user_query(raw_query)
    except Exception:
        return []
    if not ast:
        return []
    positives, _negatives = split_positive_negative(ast)
    for t in positives:
        original = (t.original or t.value).strip()
        if not original:
            continue
        patterns.add(original)
        if " " in original and "-" not in original:
            patterns.add(original.replace(" ", "-"))
        if "-" in original:
            patterns.add(original.replace("-", " "))
    return sorted(patterns, key=len, reverse=True)

def highlight_html(escaped_text: str, patterns: List[str]) -> str:
    if not patterns or not escaped_text:
        return escaped_text
    text = escaped_text
    for pat in patterns:
        if not pat:
            continue
        rx = re.compile(r'(?i)(' + re.escape(pat) + r')')
        def repl(m):
            return f"<b>{m.group(1)}</b>"
        text = rx.sub(repl, text)
    return text

def make_summary_snippet(summary: str, patterns: List[str], max_len: int = 180) -> str:
    if not summary or not patterns:
        return ""
    lower = summary.lower()
    first_pos = None
    for p in patterns:
        idx = lower.find(p.lower())
        if idx != -1 and (first_pos is None or idx < first_pos):
            first_pos = idx
    if first_pos is None:
        return ""
    start = max(0, first_pos - 40)
    end = start + max_len
    snippet = summary[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(summary):
        snippet = snippet + "…"
    return highlight_html(snippet, patterns)

def format_item_line(item: dict, idx: int, patterns: Optional[List[str]] = None, include_summary=False) -> str:
    title = clean_text(item.get('title') or "")
    if patterns:
        title = highlight_html(title, patterns)
    source = clean_text(item.get('source') or "")
    published = clean_text(item.get('published') or "")
    line = f"{idx}. [{source}] {title}"
    if published:
        line += f"\n{published}"
    if include_summary:
        raw_summary = clean_text(item.get('summary') or "")
        snippet = make_summary_snippet(raw_summary, patterns or [])
        if snippet:
            line += f"\n{snippet}"
    return line

def build_search_page_text(items: list, offset: int, limit: int, total: int,
                           header: str, patterns: List[str]) -> str:
    header = clean_text(header)
    total_pages = max(1, (total + limit - 1) // limit)
    current_page = (offset // limit) + 1
    if not items:
        if total == 0:
            return f"{header}\nНет данных."
        return f"{header}\nСтр. {current_page}/{total_pages} пустая."
    lines = [f"{header}\nРезультаты {offset+1}–{offset+len(items)} из {total} (стр. {current_page}/{total_pages})"]
    for i, it in enumerate(items, start=1):
        lines.append(format_item_line(it, offset + i, patterns, include_summary=True))
    return "\n\n".join(lines)

def build_page_text(items: list, offset: int, limit: int, total: int, header: str) -> str:
    header = clean_text(header)
    if not items:
        if total == 0:
            return f"{header}\nНет данных."
        return f"{header}\nЭта страница пуста."
    lines = [f"{header}\nПозиции {offset+1}–{offset+len(items)} из {total}"]
    for i, it in enumerate(items, start=1):
        lines.append(format_item_line(it, offset + i))
    return "\n\n".join(lines)

def build_news_keyboard(items: list, offset: int, limit: int, total: int):
    buttons = []
    for i, it in enumerate(items, start=1):
        buttons.append([InlineKeyboardButton(text=f"🔗 {offset + i}", url=it["link"])])
    has_prev = offset > 0
    has_next = (offset + limit) < total
    nav_row = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                text="« Пред",
                callback_data=LatestPage(offset=max(0, offset - limit), limit=limit).pack(),
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text="След »",
                callback_data=LatestPage(offset=offset + limit, limit=limit).pack(),
            )
        )
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="lp:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_search_keyboard(items: list, key: str, offset: int, limit: int, total: int):
    buttons = []
    for i, it in enumerate(items, start=1):
        buttons.append([InlineKeyboardButton(text=f"🔗 {offset + i}", url=it["link"])])
    has_prev = offset > 0
    has_next = (offset + limit) < total
    nav_row = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                text="« Пред",
                callback_data=FilterPage(key=key, offset=max(0, offset - limit), limit=limit).pack(),
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text="След »",
                callback_data=FilterPage(key=key, offset=offset + limit, limit=limit).pack(),
            )
        )
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="fs:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_source_keyboard(items: list, key: str, offset: int, limit: int, total: int):
    buttons = []
    for i, it in enumerate(items, start=1):
        buttons.append([InlineKeyboardButton(text=f"🔗 {offset + i}", url=it["link"])])
    has_prev = offset > 0
    has_next = (offset + limit) < total
    nav_row = []
    if has_prev:
        nav_row.append(
            InlineKeyboardButton(
                text="« Пред",
                callback_data=SourcePage(key=key, offset=max(0, offset - limit), limit=limit).pack(),
            )
        )
    if has_next:
        nav_row.append(
            InlineKeyboardButton(
                text="След »",
                callback_data=SourcePage(key=key, offset=offset + limit, limit=limit).pack(),
            )
        )
    if nav_row:
        buttons.append(nav_row)
    buttons.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="sp:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_single_news_text(item: dict, idx: int, total: int) -> str:
    title = clean_text(item.get("title") or "")
    source = clean_text(item.get("source") or "")
    published = clean_text(item.get("published") or "")
    summary = clean_text(item.get("summary") or "")
    parts = [
        f"Новость {idx+1} из {total}",
        f"[{source}] {title}",
        published,
        summary
    ]
    return safe_join(parts)

def build_single_news_keyboard(item: dict, idx: int, total: int):
    buttons = []
    buttons.append([InlineKeyboardButton(text="🔗 Перейти", url=item["link"])])
    nav_rows = []

    left_row = []
    if idx > 0:
        left_row.append(
            InlineKeyboardButton(
                text="⏮ Перв.",
                callback_data=NewsItem(idx=0).pack()
            )
        )
        left_row.append(
            InlineKeyboardButton(
                text="« Пред",
                callback_data=NewsItem(idx=idx - 1).pack()
            )
        )
    if left_row:
        nav_rows.append(left_row)

    right_row = []
    if idx < total - 1:
        right_row.append(
            InlineKeyboardButton(
                text="След »",
                callback_data=NewsItem(idx=idx + 1).pack()
            )
        )
        right_row.append(
            InlineKeyboardButton(
                text="Посл. ⏭",
                callback_data=NewsItem(idx=total - 1).pack()
            )
        )
    if right_row:
        nav_rows.append(right_row)

    buttons.extend(nav_rows)
    buttons.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="ni:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def build_source_single_news_text(item: dict, idx: int, total: int, source: str) -> str:
    title = clean_text(item.get("title") or "")
    source_clean = clean_text(source)
    published = clean_text(item.get("published") or "")
    summary = clean_text(item.get("summary") or "")
    parts = [
        f"Источник: [{source_clean}] — новость {idx+1} из {total}",
        title,
        published,
        summary
    ]
    return safe_join(parts)

def build_source_single_news_keyboard(item: dict, idx: int, total: int, key: str):
    buttons = []
    buttons.append([InlineKeyboardButton(text="🔗 Перейти", url=item["link"])])
    nav_rows = []

    left_row = []
    if idx > 0:
        left_row.append(
            InlineKeyboardButton(
                text="⏮ Перв.",
                callback_data=SourceNewsItem(key=key, idx=0).pack()
            )
        )
        left_row.append(
            InlineKeyboardButton(
                text="« Пред",
                callback_data=SourceNewsItem(key=key, idx=idx - 1).pack()
            )
        )
    if left_row:
        nav_rows.append(left_row)

    right_row = []
    if idx < total - 1:
        right_row.append(
            InlineKeyboardButton(
                text="След »",
                callback_data=SourceNewsItem(key=key, idx=idx + 1).pack()
            )
        )
        right_row.append(
            InlineKeyboardButton(
                text="Посл. ⏭",
                callback_data=SourceNewsItem(key=key, idx=total - 1).pack()
            )
        )
    if right_row:
        nav_rows.append(right_row)

    buttons.extend(nav_rows)
    buttons.append([InlineKeyboardButton(text="✖ Закрыть", callback_data="sni:close")])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

async def _maybe_call(func):
    try:
        if inspect.iscoroutinefunction(func):
            return await func()
        return func()
    except Exception:
        return None

def setup_handlers(
    db,
    fetch_trigger: Callable,
    chat_id_admin: int | None = None,
    page_size: int = 10,
    search_page_size: int | None = None,
    latest_count: int | None = None,
):
    if search_page_size is None:
        search_page_size = page_size
    if latest_count is None:
        latest_count = page_size

    SEARCH_CACHE: Dict[str, str] = {}
    SOURCE_CACHE: Dict[str, str] = {}

    @router.message(Command("help"))
    @router.message(Command("start"))
    async def help_cmd(message: Message):
        await message.answer(HELP_TEXT)

    @router.message(Command("latest"))
    async def latest_cmd(message: Message):
        limit = max(1, latest_count)
        items = db.latest(limit)
        total = db.total()
        text = build_page_text(items, 0, limit, total, header=f"Последние {limit} новостей")
        kb = build_news_keyboard(items, 0, limit, total)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    @router.message(Command("news"))
    async def news_cmd(message: Message):
        total = db.total()
        if total == 0:
            await message.answer("Нет новостей.")
            return
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1:
            try:
                idx_user = int(parts[1])
                idx = idx_user - 1
            except (ValueError, TypeError):
                idx = 0
        else:
            idx = 0
        if idx < 0:
            idx = 0
        if idx >= total:
            idx = total - 1
        items = db.latest_page(idx, 1)
        if not items:
            await message.answer("Нет данных.")
            return
        item = items[0]
        text = build_single_news_text(item, idx, total)
        kb = build_single_news_keyboard(item, idx, total)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    @router.message(Command("stats"))
    async def stats_cmd(message: Message):
        stats = db.count_by_source()
        total = db.total()
        lines = [f"Всего новостей: {total}"]
        for s in stats:
            lines.append(f"{clean_text(s['source'])}: {s['count']}")
        await message.answer("\n".join(lines))

    @router.message(Command("sources"))
    async def sources_cmd(message: Message):
        stats = db.count_by_source()
        if not stats:
            await message.answer("Нет источников.")
            return
        lines = ["Источники (кол-во):"]
        for s in stats:
            lines.append(f"- {clean_text(s['source'])} ({s['count']})")
        await message.answer("\n".join(lines))

    @router.message(Command("fetch"))
    async def fetch_cmd(message: Message):
        if chat_id_admin and message.from_user and message.from_user.id != chat_id_admin:
            await message.answer("Недостаточно прав.")
            return
        await message.answer("Запускаю сбор...")
        added_map = await fetch_trigger()
        total_new = sum(added_map.values())
        if total_new == 0:
            await message.answer("Новых новостей не найдено.")
        else:
            lines = [f"Новые новости: {total_new}"]
            for k, v in added_map.items():
                if v:
                    lines.append(f"- {clean_text(k)}: {v}")
            await message.answer("\n".join(lines))

    @router.message(Command("arc_months"))
    async def arc_months_cmd(message: Message):
        months = None
        for attr in ("list_archive_months", "archive_months", "list_arc_months"):
            if hasattr(db, attr):
                months = await _maybe_call(getattr(db, attr))
                if months is not None:
                    break
        if not months:
            await message.answer("Архив недоступен или нет данных.")
            return
        if isinstance(months, list):
            extracted = []
            for m in months:
                if isinstance(m, dict):
                    val = m.get("month") or m.get("m")
                    if val:
                        extracted.append(str(val))
                else:
                    extracted.append(str(m))
            months = sorted(set(extracted))
        await message.answer("Доступные месяцы:\n" + "\n".join(months[:200]))

    @router.message(Command("archive_now"))
    async def archive_now_cmd(message: Message):
        if chat_id_admin and message.from_user and message.from_user.id != chat_id_admin:
            await message.answer("Недостаточно прав.")
            return
        await message.answer("Запуск архивации...")
        archiver = None
        for attr in ("archive_now", "run_archive", "build_archive", "make_archive"):
            if hasattr(db, attr):
                archiver = getattr(db, attr)
                break
        if not archiver:
            await message.answer("Функция архивации не найдена.")
            return
        result = await _maybe_call(archiver)
        if result is None:
            await message.answer("Архивация завершена (подробности недоступны).")
        elif isinstance(result, dict):
            lines = ["Архивация завершена:"]
            for k, v in result.items():
                lines.append(f"- {clean_text(str(k))}: {v}")
            await message.answer("\n".join(lines))
        else:
            await message.answer(f"Архивация завершена: {clean_text(str(result))}")

    @router.message(Command("filter"))
    async def filter_cmd(message: Message):
        raw_part = message.text[len("/filter"):]
        if not raw_part:
            await message.answer("Пустой запрос.")
            return
        page = 1
        query = raw_part
        if "|" in raw_part:
            q_part, page_part = raw_part.rsplit("|", 1)
            if page_part.strip().isdigit():
                p = int(page_part.strip())
                if p > 0:
                    page = p
                    query = q_part.strip()
        if not query:
            await message.answer("Пустой запрос.")
            return
        if parse_user_query:
            try:
                ast = parse_user_query(query)
                if not ast:
                    await message.answer("Пустой запрос.")
                    return
            except Exception:
                pass

        norm_for_key = re.sub(r"\s+", " ", query.lower()).strip()
        key = hashlib.sha1(norm_for_key.encode("utf-8")).hexdigest()[:8]
        SEARCH_CACHE[key] = query

        limit = search_page_size
        offset = (page - 1) * limit
        rows, total = db.search(query, limit, offset)
        patterns = build_highlight_patterns(query)
        header = f"Поиск: “{query}”"
        text = build_search_page_text(rows, offset, limit, total, header, patterns)
        kb = build_search_keyboard(rows, key, offset, limit, total)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    @router.message(Command("source"))
    async def source_cmd(message: Message):
        raw_part = message.text[len("/source"):]
        if not raw_part:
            await message.answer("Использование: /source &lt;источник&gt; [N] | пример: /source meduza 5")
            return
        
        # Parse source and optional news number
        parts = raw_part.strip().split()
        if not parts:
            await message.answer("Пустой источник.")
            return
        
        src = parts[0]
        idx_user = 1  # Default to first news item
        if len(parts) > 1:
            try:
                idx_user = int(parts[1])
            except (ValueError, TypeError):
                idx_user = 1
        
        if not src:
            await message.answer("Пустой источник.")
            return

        stats = db.count_by_source()
        all_sources_lower = {s['source'].lower(): s['source'] for s in stats}
        exact = all_sources_lower.get(src.lower())
        if not exact:
            candidates = [s['source'] for s in stats if src.lower() in s['source'].lower()]
            if not candidates:
                await message.answer("Источник не найден. /sources для списка.")
                return
            if len(candidates) > 1:
                lines = ["Найдено несколько источников:"]
                for c in candidates[:20]:
                    lines.append(f"- {clean_text(c)}")
                if len(candidates) > 20:
                    lines.append("... (урезано)")
                lines.append("Уточните: /source <точное_имя>")
                await message.answer("\n".join(lines))
                return
            exact = candidates[0]

        total = db.total_by_source(exact)
        if total == 0:
            await message.answer(f"Нет новостей для источника: {clean_text(exact)}")
            return
        
        # Convert to 0-based index and validate
        idx = idx_user - 1
        if idx < 0:
            idx = 0
        if idx >= total:
            idx = total - 1

        norm_key = hashlib.sha1(exact.lower().encode("utf-8")).hexdigest()[:8]
        SOURCE_CACHE[norm_key] = exact

        # Get the specific news item
        items = db.source_news(exact, 1, idx)
        if not items:
            await message.answer("Нет данных.")
            return
        
        item = items[0]
        text = build_source_single_news_text(item, idx, total, exact)
        kb = build_source_single_news_keyboard(item, idx, total, norm_key)
        await message.answer(text, reply_markup=kb, disable_web_page_preview=True)

    @router.callback_query()
    async def pagination_callback(cb: CallbackQuery):
        if not cb.data:
            return

        if cb.data in {"lp:close", "fs:close", "ni:close", "sp:close", "sni:close"}:
            try:
                await cb.message.edit_reply_markup(reply_markup=None)
            except Exception:
                pass
            await cb.answer("Закрыто")
            return

        if cb.data.startswith("lp:"):
            try:
                data = LatestPage.unpack(cb.data)
            except Exception:
                await cb.answer("Ошибка данных", show_alert=False)
                return
            offset = data.offset
            limit = data.limit
            items = db.latest_page(offset, limit)
            total = db.total()
            text = build_page_text(items, offset, limit, total, header="Новости")
            kb = build_news_keyboard(items, offset, limit, total)
            try:
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                await cb.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            await cb.answer()
            return

        if cb.data.startswith("fs:"):
            try:
                data = FilterPage.unpack(cb.data)
            except Exception:
                await cb.answer("Ошибка данных", show_alert=False)
                return
            key = data.key
            offset = data.offset
            limit = data.limit
            if key not in SEARCH_CACHE:
                await cb.answer("Сессия поиска устарела. Повторите /filter.", show_alert=True)
                return
            raw_query = SEARCH_CACHE[key]
            rows, total = db.search(raw_query, limit, offset)
            patterns = build_highlight_patterns(raw_query)
            header = f"Поиск: “{raw_query}”"
            text = build_search_page_text(rows, offset, limit, total, header, patterns)
            kb = build_search_keyboard(rows, key, offset, limit, total)
            try:
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                await cb.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            await cb.answer()
            return

        if cb.data.startswith("sp:"):
            try:
                data = SourcePage.unpack(cb.data)
            except Exception:
                await cb.answer("Ошибка данных", show_alert=False)
                return
            key = data.key
            offset = data.offset
            limit = data.limit
            if key not in SOURCE_CACHE:
                await cb.answer("Сессия устарела. Повторите /source.", show_alert=True)
                return
            source = SOURCE_CACHE[key]
            total = db.total_by_source(source)
            rows = db.source_news(source, limit, offset)
            total_pages = max(1, (total + limit - 1) // limit)
            current_page = (offset // limit) + 1
            header = f"Источник: [{clean_text(source)}] (стр. {current_page}/{total_pages}, всего {total})"
            text = build_page_text(rows, offset, limit, total, header=header)
            kb = build_source_keyboard(rows, key, offset, limit, total)
            try:
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                await cb.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            await cb.answer()
            return

        if cb.data.startswith("ni:"):
            try:
                data = NewsItem.unpack(cb.data)
            except Exception:
                await cb.answer("Ошибка данных", show_alert=False)
                return
            idx = data.idx
            total = db.total()
            if total == 0:
                await cb.answer("Нет данных.", show_alert=False)
                return
            if idx < 0:
                idx = 0
            if idx >= total:
                idx = total - 1
            items = db.latest_page(idx, 1)
            if not items:
                await cb.answer("Нет данных.", show_alert=False)
                return
            item = items[0]
            text = build_single_news_text(item, idx, total)
            kb = build_single_news_keyboard(item, idx, total)
            try:
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                await cb.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            await cb.answer()
            return

        if cb.data.startswith("sni:"):
            try:
                data = SourceNewsItem.unpack(cb.data)
            except Exception:
                await cb.answer("Ошибка данных", show_alert=False)
                return
            key = data.key
            idx = data.idx
            if key not in SOURCE_CACHE:
                await cb.answer("Сессия устарела. Повторите /source.", show_alert=True)
                return
            source = SOURCE_CACHE[key]
            total = db.total_by_source(source)
            if total == 0:
                await cb.answer("Нет данных.", show_alert=False)
                return
            if idx < 0:
                idx = 0
            if idx >= total:
                idx = total - 1
            items = db.source_news(source, 1, idx)
            if not items:
                await cb.answer("Нет данных.", show_alert=False)
                return
            item = items[0]
            text = build_source_single_news_text(item, idx, total, source)
            kb = build_source_single_news_keyboard(item, idx, total, key)
            try:
                await cb.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
            except Exception:
                await cb.message.answer(text, reply_markup=kb, disable_web_page_preview=True)
            await cb.answer()
            return
