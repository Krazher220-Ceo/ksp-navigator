# Учебный навигатор — этап 1 (КСП-генератор)

Telegram-бот и Mini App, которые собирают черновик краткосрочного
поурочного плана (КСП) по форме приказа МОН РК №130 — по прошлым КСП
учителя (профиль стиля) и коду цели обучения из КТП.

Единственный источник правды по проекту в целом — [`MASTER.md`](MASTER.md).
План работ этого этапа, блок за блоком — [`PLAN_STAGE1.md`](PLAN_STAGE1.md).
Этот файл — только про то, как поднять уже написанный код на своей машине.

**Копипаст.** Все команды ниже — блоками без комментариев `#` внутри.
В интерактивном zsh (не в `.sh`-скрипте, а когда вставляешь текст прямо
в приглашение терминала) строка с `#` — это попытка выполнить команду
с именем `#`, не комментарий, отсюда `zsh: command not found: #` при
вставке. Пояснения — отдельным текстом до блока, сам блок — только
команды, целиком безопасен для вставки одним куском.

---

## 1. Установка с нуля на чистом Mac

```bash
git clone https://github.com/Krazher220-Ceo/ksp-navigator.git
cd ksp-navigator
bash scripts/setup_mac.sh
```

Скрипт ставит через Homebrew `python@3.11`, `libreoffice` (конвертация
`.doc`→`.docx` — `pandoc` для этого не годится, он не читает бинарный
`.doc`), `cloudflared`, создаёт `venv/` и ставит зависимости из
`requirements.txt`. Идемпотентен — можно запускать повторно.

Первый запуск скачивает LibreOffice (~1 ГБ) — рассчитывай на это время.

Проверить, что окружение поднялось:

```bash
source venv/bin/activate
python -c "import aiogram, fastapi, docx, openpyxl; print('OK')"
```

---

## 2. Токен бота у @BotFather

В Telegram — диалог с [@BotFather](https://t.me/BotFather):

```
/newbot
```

Дальше по шагам (имя бота, username, заканчивающийся на `bot`).
В конце BotFather пришлёт токен вида `123456789:AAE...` — он и идёт в
`.env` следующим шагом.

---

## 3. Настройка `.env`

`scripts/setup_mac.sh` (шаг 1) уже создал `.env` из `.env.example` сам,
если его ещё не было — проверяет и копирует только при отсутствии файла,
второй раз не перезапишет. Если `.env` почему-то нет:

```bash
test -f .env || cp .env.example .env
```

Открой `.env` любым редактором и заполни:

| Переменная | Что вписать |
|---|---|
| `TELEGRAM_BOT_TOKEN` | токен из шага 2 |
| `DEEPSEEK_API_KEY` | ключ DeepSeek (основной провайдер, платный) |
| `DEEPSEEK_MODEL` | уже стоит `deepseek-v4-flash` — актуальный список моделей смотри на platform.deepseek.com, названия меняются |
| `GEMINI_API_KEY` / `GROK_API_KEY` / `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | резервные провайдеры на бесплатных лимитах — необязательны для старта, но без хотя бы одного резервного `LLM_PROVIDERS` из `core/llm_client.py` (блок Б2) не сможет переключиться, если DeepSeek окажется недоступен |
| `WEBAPP_URL` | заполняется в шаге 6, после настройки туннеля — пока можно оставить пустым, бот заработает и без него (`/templates` просто скажет, что Mini App пока не настроен) |

Остальные переменные (`WEBAPP_PORT`, `DB_PATH`, `LOG_LEVEL`) можно
оставить как в `.env.example`.

---

## 4. База данных

```bash
python scripts/init_db.py --reset
```

Спросит подтверждение, если файл базы уже существует. Создаёт схему и
загружает `curriculum_seed.sql` — 46 целей обучения и 83 урока КТП
физики 10 класса (готовые seed-данные, не привязаны к конкретному
учителю в Telegram).

Проверка:

```bash
sqlite3 storage/app.db "SELECT COUNT(*) FROM curriculum_objectives;"
```

Ожидается `46`.

---

## 5. Встроенные шаблоны КСП

```bash
python -c "from core.templates import load_builtin_templates; print(load_builtin_templates(), 'новых шаблонов добавлено')"
```

Идемпотентно — повторный запуск не плодит дубли, просто напечатает `0`.

---

## 6. Cloudflare Tunnel и Mini App

Полная инструкция, включая реальные грабли (порядок аргументов
`cloudflared service install`, где именно лежит `config.yml`) — в
[`launchd/README.md`](launchd/README.md), раздел 1. Коротко:

```bash
cloudflared tunnel login
cloudflared tunnel create ksp
cloudflared tunnel route dns ksp ksp.example.com
```

Замени `ksp.example.com` на свой реальный поддомен и домен — тот, что
уже добавлен в Cloudflare как зона.

Именованному туннелю нужен домен, добавленный в Cloudflare (Cloudflare
выступает DNS для него) — без этого `route dns` неоткуда взять зону.
Быстрый режим `cloudflared tunnel --url` **не подходит**: адрес меняется
при каждом перезапуске, а регистрируется у @BotFather один раз.

Дальше — конфиг, служба, второй агент под сам веб-сервер — всё в
`launchd/README.md`, раздел 1 целиком, по шагам, с точными путями.

После того как `curl -I https://ksp.example.com` (со своим реальным
доменом вместо примера) отвечает `200`/`401` (не `530`) — вписать в
`.env` свой реальный адрес:

```
WEBAPP_URL=https://ksp.example.com
```

И у @BotFather: `/mybots` → выбрать бота → `Bot Settings` → пункт про
Menu Button / Web App URL → вписать тот же адрес.

---

## 7. Запуск

Разово, из терминала (для проверки перед тем как ставить как службу):

```bash
source venv/bin/activate
python -m bot.main
```

`Ctrl+C` — остановить. `/start` в Telegram должен ответить.

Постоянно, переживает перезагрузку Mac — через `launchd`. Полная
инструкция (все пять агентов: keepawake, бот, веб-сервер, watchdog,
бэкап) — [`launchd/README.md`](launchd/README.md), раздел 2.

```bash
mkdir -p ~/logs
mkdir -p ~/Library/LaunchAgents
cp launchd/com.alikhan.keepawake.plist ~/Library/LaunchAgents/
cp launchd/com.alikhan.kspbot.plist ~/Library/LaunchAgents/
cp launchd/com.alikhan.webapi.plist ~/Library/LaunchAgents/
cp launchd/com.alikhan.watchdog.plist ~/Library/LaunchAgents/
cp launchd/com.alikhan.backup.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alikhan.keepawake.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.kspbot.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.webapi.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.watchdog.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.backup.plist
```

Также один раз руками — System Settings → Battery → Power Adapter →
«Prevent automatic sleeping when the display is off» (подробности —
`launchd/README.md`, раздел 2, «Системные настройки»).

---

## 8. Где смотреть логи

| Файл | Что там |
|---|---|
| `logs/app.log` (в папке проекта) | внутреннее логирование Python (`core.config` — HTTP-запросы к LLM, ошибки очереди и т.п.) |
| `~/logs/kspbot.out.log` / `.err.log` | stdout/stderr процесса бота (launchd) |
| `~/logs/webapi.out.log` / `.err.log` | stdout/stderr FastAPI-сервера Mini App |
| `~/logs/keepawake.out.log` / `.err.log` | caffeinate |
| `~/logs/watchdog.log` | проверки раз в 5 минут, строки `ТРЕВОГА` — реальная проблема (`grep ТРЕВОГА ~/logs/watchdog.log`) |
| `~/logs/backup.log` | результат ежедневного бэкапа |
| `/Library/Logs/com.cloudflare.cloudflared.err.log` | сам туннель — сюда смотреть при `530`/`502` |

---

## 9. Бот молчит — что проверять по порядку

1. **Процесс вообще жив?**
   ```bash
   launchctl list | grep com.alikhan.kspbot
   ```
   Первая колонка — PID. `-` вместо числа — процесс не запущен или
   упал и ждёт `ThrottleInterval` перед рестартом.

2. **Что в логе ошибок бота?**
   ```bash
   tail -50 ~/logs/kspbot.err.log
   ```
   `ModuleNotFoundError: No module named 'core'` — агент запущен не
   через `-m bot.main` из корня проекта (см. `launchd/README.md`,
   «Ловушка: launchd не видит PATH»). `ОШИБКА КОНФИГУРАЦИИ` — не задан
   или пуст `TELEGRAM_BOT_TOKEN`/обязательное поле в `.env`.

3. **Сеть до Telegram есть?**
   ```bash
   curl -I https://api.telegram.org
   ```
   Если нет — проблема не в боте, а в интернете/Wi-Fi/провайдере;
   `watchdog.log` должен это тоже поймать в течение 15 минут.

4. **Токен верный?** Пустой ответ от `getMe` или `401` в логе — токен
   в `.env` не совпадает с тем, что выдал @BotFather (например, скопирован
   с лишним пробелом).

5. **База доступна?**
   ```bash
   sqlite3 storage/app.db "SELECT 1;"
   ```
   Ошибка вроде `database is locked`/`disk I/O error` — реже, но
   возможна на сетевых дисках или при повреждении файла (см. ниже,
   раздел про восстановление из бэкапа).

6. **Долго не отвечает конкретная команда (`/generate`)?** Это не
   "бот молчит" в смысле процесса, а либо очередь встала, либо LLM не
   отвечает:
   ```bash
   sqlite3 storage/app.db "SELECT type, status, retries, error FROM tasks ORDER BY created_at DESC LIMIT 5;"
   ```
   `status='failed'` — учитель должен был получить сообщение об этом
   в чат (гарантируется кодом, `core/queue.py`) — если сообщения не
   было, это баг, а не ожидаемое поведение.

7. **Mini App не открывается / показывает "Не удалось загрузить"?**
   ```bash
   curl -I https://ksp.example.com
   ```
   (со своим реальным доменом вместо примера)
   `530` — см. `launchd/README.md`, раздел 1 (почти всегда: либо
   `cloudflared` службе не хватает `tunnel run` в `ProgramArguments`,
   либо `config.yml` не на месте, либо `com.alikhan.webapi` не
   загружен). `401`/`403` — это нормально для прямого `curl` без
   `initData`, открывай через настоящую кнопку в боте, не браузером
   напрямую.

---

## 10. Бэкап — снять и накатить

Снять руками (обычно это делает `launchd/com.alikhan.backup.plist`
сам, ежедневно в 03:00):

```bash
bash scripts/backup_db.sh
ls -la backup/
```

Через `sqlite3 ... .backup`, не `cp` — база в режиме WAL, обычное
копирование работающего файла может дать неполную или битую копию.
Хранится 14 дней, старше — удаляется автоматически. Подробности —
`scripts/backup_db.sh`, там же и `scripts/watchdog.sh`.

**Накатить бэкап обратно** (например после повреждения базы) — бот и
веб-сервер сначала остановить, иначе они продолжат писать в старый
файл поверх восстановленного:

```bash
launchctl unload ~/Library/LaunchAgents/com.alikhan.kspbot.plist
launchctl unload ~/Library/LaunchAgents/com.alikhan.webapi.plist
cp storage/app.db "storage/app.db.before-restore-$(date +%Y-%m-%d-%H%M).bak"
cp backup/app_2026-08-20.db storage/app.db
rm -f storage/app.db-wal storage/app.db-shm storage/app.db-journal
launchctl load ~/Library/LaunchAgents/com.alikhan.kspbot.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.webapi.plist
```

(Дату `2026-08-20` в имени файла бэкапа — подставь свою, из `ls backup/`.
Строка с `cp storage/app.db ...bak` — на всякий случай сохраняет то,
что было в базе ДО восстановления, вдруг откатывать было рано.)

Проверка, что база рабочая, после восстановления:

```bash
sqlite3 storage/app.db "PRAGMA integrity_check;"
```

Ожидается `ok`.

---

## 11. Тесты

```bash
source venv/bin/activate
pytest -v
```

Ожидается `198 passed`, ноль `skipped`. Полный список зафиксированных
KPI этапа — [`KPI_STAGE1.md`](KPI_STAGE1.md).

---

## 12. Структура проекта

```
core/       — бизнес-логика: БД, LLM-клиент, разбор КСП/КТП, шаблоны,
              сборка .docx, генератор, очередь задач. Не знает про
              Telegram и HTTP — это делают bot/ и web/.
bot/        — Telegram-бот (aiogram): команды, диалоги (FSM), тексты.
web/        — FastAPI для Mini App + сама статика Mini App (web/static/).
scripts/    — установка окружения, инициализация БД, watchdog, бэкап.
launchd/    — .plist-агенты для постоянной работы на Mac + README.md
              с полной инструкцией по инфраструктуре (туннель, службы).
storage/    — схема БД, встроенные шаблоны, сама база (не в git),
              загруженные/сгенерированные файлы (не в git).
tests/      — pytest, 198 тестов на весь код выше.
```

У каждого модуля `core/` в шапке файла — зачем он, что осознанно не
делает, на что опирается (`MASTER.md`, п.7.2). Если непонятно, с чего
начать читать код — начинать оттуда, не с реализации.

---

## 13. Чего в этапе 1 нет

Полный список — `PLAN_STAGE1.md`, раздел C. Коротко: никакой
транскрипции аудио, OCR, аналитики покрытия программы, предсказания
тем СОР/СОЧ, Supabase/pgvector, React/Next.js. Mini App — ровно два
экрана (шаблоны, предпросмотр), ничего не редактирует. Всё это —
следующие этапы, см. `MASTER.md`, часть IV.
