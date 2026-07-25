"""Dictionary-based i18n (EN / RU, switchable at runtime)."""
from __future__ import annotations

EN = {
    "app_title": "TG Channel Stats",
    # nav / sidebar
    "nav_config": "Config",
    "nav_channels": "Channels",
    "nav_no_channels": "No channels yet — fetch one from the Config screen.",
    "nav_compare": "⇄ Compare",
    "nav_compare_hint": "Pick 2-6 channels to compare.",
    "nav_fold_hint": "Collapse sidebar",
    "nav_unfold_hint": "Expand sidebar",
    "nav_lang_hint": "Switch language",
    "compare_title": "Compare channels",
    # menus
    "menu_file": "File",
    "menu_language": "Language",
    "menu_theme": "Theme",
    "import_env": "Import .env into current profile…",
    "export_env": "Export current profile as .env…",
    "open_config_folder": "Open config folder",
    "quit": "Quit",
    "lang_en": "English",
    "lang_ru": "Русский",
    "theme_system": "System",
    "theme_light": "Light",
    "theme_dark": "Dark",
    "env_imported": "Imported {n} value(s) from .env.",
    # generic
    "save": "💾 Save profile",
    "saved": "Profile saved.",
    "stop": "⏹ Stop",
    "done_ok": "✅ Finished: {msg}",
    "done_fail": "❌ Failed: {msg}",
    "cancelled": "⏹ Cancelled by user.",
    "missing_conn": "Fill in API_ID, API_HASH and PHONE_NUMBER first.",
    "worker_running": "A fetch is still running. Stop it first.",
    # profiles
    "profile": "Profile",
    "new_profile": "New…",
    "delete_profile": "Delete",
    "profile_name": "Profile name:",
    "delete_profile_confirm": "Delete profile “{name}”?",
    # connection fields
    "field_API_ID": "API_ID",
    "field_API_HASH": "API_HASH",
    "field_PHONE_NUMBER": "PHONE_NUMBER",
    "config_location": "Config file: {path}",
    "instructions_title": "📖 How to get API_ID and API_HASH",
    "instructions_text": (
        "1. Open <a href='https://my.telegram.org'>my.telegram.org</a> and log in "
        "with your phone number (Telegram sends the code to your Telegram app).<br>"
        "2. Click <b>API development tools</b>.<br>"
        "3. Fill in <i>App title</i> and <i>Short name</i> (any text), "
        "platform <i>Desktop</i>, then create the application.<br>"
        "4. Copy <b>App api_id</b> → API_ID and <b>App api_hash</b> → API_HASH.<br>"
        "5. PHONE_NUMBER must be in international format, e.g. <code>+79001234567</code>.<br><br>"
        "<b>Which channel can I analyze?</b> Any public channel by "
        "<code>@username</code>, or a private one you're a member of by its "
        "<code>-100…</code> ID or t.me link."
    ),
    # login
    "login_code_prompt": "Enter the login code Telegram just sent you:",
    "login_password_prompt": "Enter your two-factor (2FA) password:",
    "login_title": "Telegram login",
    "qr_login_button": "🔳 Login via QR code",
    "qr_login_title": "QR code login",
    "qr_login_hint": "Open Telegram on your phone → Settings → Devices → "
                     "Link Desktop Device, then scan this code.",
    "qr_login_generating": "Generating QR code…",
    "qr_login_waiting": "Waiting for you to scan…",
    "qr_login_expired": "Code expired, generating a new one…",
    "qr_login_success": "Logged in successfully.",
    "check_login_button": "🔎 Check login",
    "check_login_checking": "Checking…",
    "check_login_ok": "✅ Logged in as {name} (+{phone}).",
    "check_login_not_authorized": "❌ Not logged in — use QR login first.",
    # fetch card
    "fetch_title": "Fetch a channel",
    "fetch_help": "Scans a public/private channel over the chosen period: reads "
                  "each post's views, reactions and forwards, ranks the top "
                  "posts, and computes activity stats (members, posts/day, "
                  "hour & weekday patterns). The result is stored as a "
                  "checkpoint and added to the sidebar.",
    "fetch_channel": "Channel ID or @username",
    "fetch_channel_placeholder": "@durov or -1001234567890",
    "fetch_top_n": "Keep top N per metric",
    "fetch_period": "Period of analysis",
    "period_3m": "3 months",
    "period_6m": "6 months",
    "period_1y": "1 year",
    "period_2y": "2 years",
    "period_3y": "3 years",
    "period_all": "All time",
    "fetch_public": "Fetch public reposts (slower, needs stats access)",
    "fetch_button": "📊 Fetch & analyze",
    "fetch_log": "Log",
    "fetch_done": "Analyzed {title}: {n} top post(s) out of {scanned} scanned.",
    # dashboard header
    "dash_download": "Export",
    "dash_fetched_at": "Fetched {when}",
    "dash_refresh": "🔄 Re-fetch",
    "dash_remove": "🗑 Remove",
    "dash_remove_confirm": "Remove “{name}” from the sidebar? "
                           "(The stored checkpoint file is deleted.)",
    "dash_period_label": "Period: {period}",
    "dash_created_label": "Created {when}",
    # stat cards
    "stat_members": "Members",
    "stat_total_posts": "Posts",
    "stat_total_posts_period": "Posts ({period})",
    "stat_avg_views": "Avg. views",
    "stat_max_views": "Max views",
    "stat_posts_per_day": "Posts / day",
    "stat_avg_reactions": "Avg. reactions",
    "stat_avg_reposts": "Avg. reposts",
    "stat_max_reposts": "Max reposts",
    "stat_created": "Created",
    # compare-mode calculated cards
    "stat_views_per_member": "Avg. views / member",
    "stat_reposts_per_post": "Avg. repost / post",
    "stat_err_pct": "ERR%",
    "stat_err_pct_sub": "2 weeks+",
    "cmp_max_views": "Max views / post",
    "cmp_posts_per_day": "Post / day",
    "cmp_max_reposts": "Max repost / post",
    "cmp_avg_reactions": "Avg. reactions / post",
    "cmp_view_repost_year": "Views / Reposts {year}",
    # charts
    "chart_activity": "Month activity",
    "chart_by_hour": "Posts by hour of day",
    "chart_by_weekday": "Posts by day of week",
    "chart_empty": "No data",
    # weekday short labels (Mon..Sun order)
    "wd_mon": "Mon", "wd_tue": "Tue", "wd_wed": "Wed", "wd_thu": "Thu",
    "wd_fri": "Fri", "wd_sat": "Sat", "wd_sun": "Sun",
    # top posts table (from channel_top)
    "top_posts_title": "Top posts",
    "col_date": "Date",
    "col_post": "Post",
    "col_views": "Views",
    "col_reactions": "Reactions",
    "col_private": "Private reposts",
    "col_public": "Public reposts",
    "album_suffix": "  ·  album ({n} items)",
    "show": "Show",
    "public_na": "n/a",
    "public_off": "—",
    "public_title": "Public reposts of post #{id}",
    "public_empty": "No public reposts found for this post.",
    "public_col_channel": "Channel",
    "public_col_views": "Views",
    "public_col_link": "Link",
    # exports (kept from channel_top)
    "report_button": "📋 Text report",
    "report_empty": "Nothing to report yet.",
    "report_dialog_title": "Analytics report",
    "report_copy": "📋 Copy to clipboard",
    "report_title": "Analytics of channel {title}",
    "report_private": "Top 7 {emoji} Reposts",
    "report_views": "Top 7 {emoji} Views",
    "report_reactions": "Top 7 {emoji} Reactions",
    "save_md_button": "💾 Save MD",
    "md_saved": "Saved: {path}",
}

RU = {
    "app_title": "TG Channel Stats",
    "nav_config": "Настройки",
    "nav_channels": "Каналы",
    "nav_no_channels": "Пока нет каналов — загрузите канал на экране «Настройки».",
    "nav_compare": "⇄ Сравнить",
    "nav_compare_hint": "Выберите 2-6 каналов для сравнения.",
    "nav_fold_hint": "Свернуть панель",
    "nav_unfold_hint": "Развернуть панель",
    "nav_lang_hint": "Переключить язык",
    "compare_title": "Сравнение каналов",
    "menu_file": "Файл",
    "menu_language": "Язык",
    "menu_theme": "Тема",
    "import_env": "Импортировать .env в текущий профиль…",
    "export_env": "Экспортировать профиль как .env…",
    "open_config_folder": "Открыть папку настроек",
    "quit": "Выход",
    "lang_en": "English",
    "lang_ru": "Русский",
    "theme_system": "Как в системе",
    "theme_light": "Светлая",
    "theme_dark": "Тёмная",
    "env_imported": "Импортировано значений из .env: {n}.",
    "save": "💾 Сохранить профиль",
    "saved": "Профиль сохранён.",
    "stop": "⏹ Остановить",
    "done_ok": "✅ Готово: {msg}",
    "done_fail": "❌ Ошибка: {msg}",
    "cancelled": "⏹ Остановлено пользователем.",
    "missing_conn": "Сначала заполните API_ID, API_HASH и PHONE_NUMBER.",
    "worker_running": "Загрузка ещё выполняется. Сначала остановите её.",
    "profile": "Профиль",
    "new_profile": "Новый…",
    "delete_profile": "Удалить",
    "profile_name": "Имя профиля:",
    "delete_profile_confirm": "Удалить профиль «{name}»?",
    "field_API_ID": "API_ID",
    "field_API_HASH": "API_HASH",
    "field_PHONE_NUMBER": "PHONE_NUMBER",
    "config_location": "Файл настроек: {path}",
    "instructions_title": "📖 Как получить API_ID и API_HASH",
    "instructions_text": (
        "1. Откройте <a href='https://my.telegram.org'>my.telegram.org</a> и войдите "
        "по номеру телефона (код придёт в приложение Telegram).<br>"
        "2. Нажмите <b>API development tools</b>.<br>"
        "3. Заполните <i>App title</i> и <i>Short name</i> (любой текст), "
        "платформа <i>Desktop</i>, создайте приложение.<br>"
        "4. Скопируйте <b>App api_id</b> → API_ID и <b>App api_hash</b> → API_HASH.<br>"
        "5. PHONE_NUMBER — в международном формате, например <code>+79001234567</code>.<br><br>"
        "<b>Какой канал можно анализировать?</b> Любой публичный по "
        "<code>@username</code> или приватный, где вы состоите — по его "
        "<code>-100…</code> ID или ссылке t.me."
    ),
    "login_code_prompt": "Введите код входа, который прислал Telegram:",
    "login_password_prompt": "Введите пароль двухфакторной аутентификации (2FA):",
    "login_title": "Вход в Telegram",
    "qr_login_button": "🔳 Вход по QR-коду",
    "qr_login_title": "Вход по QR-коду",
    "qr_login_hint": "Откройте Telegram на телефоне → Настройки → Устройства → "
                     "Подключить устройство и отсканируйте этот код.",
    "qr_login_generating": "Генерация QR-кода…",
    "qr_login_waiting": "Ожидание сканирования…",
    "qr_login_expired": "Код истёк, генерируется новый…",
    "qr_login_success": "Вход выполнен успешно.",
    "check_login_button": "🔎 Проверить вход",
    "check_login_checking": "Проверка…",
    "check_login_ok": "✅ Вход выполнен как {name} (+{phone}).",
    "check_login_not_authorized": "❌ Не выполнен вход — используйте QR-код.",
    "fetch_title": "Загрузить канал",
    "fetch_help": "Сканирует публичный/приватный канал за выбранный период: "
                  "читает просмотры, реакции и репосты каждого поста, отбирает "
                  "лучшие и считает статистику активности (участники, "
                  "постов/день, паттерны по часам и дням недели). Результат "
                  "сохраняется как контрольная точка и добавляется в боковую "
                  "панель.",
    "fetch_channel": "ID канала или @username",
    "fetch_channel_placeholder": "@durov или -1001234567890",
    "fetch_top_n": "Оставить топ-N по каждой метрике",
    "fetch_period": "Период анализа",
    "period_3m": "3 месяца",
    "period_6m": "6 месяцев",
    "period_1y": "1 год",
    "period_2y": "2 года",
    "period_3y": "3 года",
    "period_all": "Всё время",
    "fetch_public": "Запросить публичные репосты (медленнее, нужен доступ к статистике)",
    "fetch_button": "📊 Загрузить и проанализировать",
    "fetch_log": "Журнал",
    "fetch_done": "Проанализирован {title}: {n} лучших постов из {scanned}.",
    "dash_download": "Экспорт",
    "dash_fetched_at": "Загружено {when}",
    "dash_refresh": "🔄 Обновить",
    "dash_remove": "🗑 Удалить",
    "dash_remove_confirm": "Убрать «{name}» из боковой панели? "
                           "(Файл контрольной точки будет удалён.)",
    "dash_period_label": "Период: {period}",
    "dash_created_label": "Создан {when}",
    "stat_members": "Участники",
    "stat_total_posts": "Постов",
    "stat_total_posts_period": "Постов ({period})",
    "stat_avg_views": "Ср. просмотры",
    "stat_max_views": "Макс. просмотры",
    "stat_posts_per_day": "Постов / день",
    "stat_avg_reactions": "Ср. реакции",
    "stat_avg_reposts": "Ср. репосты",
    "stat_max_reposts": "Макс. репосты",
    "stat_created": "Создан",
    "stat_views_per_member": "Ср. просмотры / подписчика",
    "stat_reposts_per_post": "Ср. репост / пост",
    "stat_err_pct": "ERR%",
    "stat_err_pct_sub": "2 нед.+",
    "cmp_max_views": "Макс. просмотры / пост",
    "cmp_posts_per_day": "Постов / день",
    "cmp_max_reposts": "Макс. репост / пост",
    "cmp_avg_reactions": "Ср. реакции / пост",
    "cmp_view_repost_year": "Просмотры / Репосты {year}",
    "chart_activity": "Активность по месяцам",
    "chart_by_hour": "Посты по часам суток",
    "chart_by_weekday": "Посты по дням недели",
    "chart_empty": "Нет данных",
    "wd_mon": "Пн", "wd_tue": "Вт", "wd_wed": "Ср", "wd_thu": "Чт",
    "wd_fri": "Пт", "wd_sat": "Сб", "wd_sun": "Вс",
    "top_posts_title": "Топ постов",
    "col_date": "Дата",
    "col_post": "Пост",
    "col_views": "Просмотры",
    "col_reactions": "Реакции",
    "col_private": "Личные репосты",
    "col_public": "Публичные репосты",
    "album_suffix": "  ·  альбом ({n} элементов)",
    "show": "Показать",
    "public_na": "н/д",
    "public_off": "—",
    "public_title": "Публичные репосты поста #{id}",
    "public_empty": "Публичных репостов этого поста не найдено.",
    "public_col_channel": "Канал",
    "public_col_views": "Просмотры",
    "public_col_link": "Ссылка",
    "report_button": "📋 Текстовый отчёт",
    "report_empty": "Пока нечего показывать.",
    "report_dialog_title": "Аналитический отчёт",
    "report_copy": "📋 Скопировать",
    "report_title": "Аналитика канала {title}",
    "report_private": "Топ 7 {emoji} Репостов",
    "report_views": "Топ 7 {emoji} Просмотров",
    "report_reactions": "Топ 7 {emoji} Реакций",
    "save_md_button": "💾 Сохранить MD",
    "md_saved": "Сохранено: {path}",
}

LANGS = {"en": EN, "ru": RU}


class I18n:
    def __init__(self, lang: str = "en") -> None:
        self.lang = lang if lang in LANGS else "en"

    def tr(self, key: str, **kw) -> str:
        text = LANGS[self.lang].get(key) or EN.get(key) or key
        return text.format(**kw) if kw else text
