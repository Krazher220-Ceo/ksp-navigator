# Инфраструктура: Mac как сервер (блок Б11)

Всё в этом файле выполняется руками, в терминале, самим автором. Ассистент
не может пройти `cloudflared tunnel login` (открывает браузер для входа в
твой личный аккаунт Cloudflare) и не устанавливает системные службы без
спроса — это тот самый случай.

**Важно про копипаст.** Блоки команд ниже — без комментариев `#` внутри
себя. Это не для красоты: в интерактивном zsh (не в `.sh`-скрипте, а
именно когда вставляешь текст прямо в приглашение терминала) строка,
начинающаяся с `#`, — это не комментарий, а попытка выполнить команду
с именем `#`, поэтому и `zsh: command not found: #` на каждой такой
строке. Пояснения теперь идут отдельным текстом до блока, а сам блок —
только команды, целиком безопасен для выделения и вставки одним куском.
(Если хочешь, чтобы `#`-комментарии работали и при вставке — один раз
добавь `setopt interactive_comments` в `~/.zshrc`, необязательно.)

---

## 1. Cloudflare Tunnel — именованный, не быстрый

### Почему не `cloudflared tunnel --url`

Быстрый режим (`cloudflared tunnel --url http://localhost:8000`) выдаёт
случайный адрес вида `https://random-words-1234.trycloudflare.com`,
который **меняется при каждом перезапуске туннеля**. URL Mini App
регистрируется у @BotFather один раз — с плавающим адресом всё отвалится
после первого же перезапуска или перезагрузки Mac. Поэтому нужен
**именованный** туннель: у него фиксированный ID и (после привязки к
домену) фиксированный адрес.

### Твой туннель уже создан

По логу видно, что это уже сделано и прошло успешно:

- вход выполнен (`cloudflared tunnel login`);
- туннель `ksp` создан, id `7aa529d1-e41e-4538-9e45-a2f2d08a2edc`,
  credentials лежат в `/Users/kr220/.cloudflared/7aa529d1-e41e-4538-9e45-a2f2d08a2edc.json`
  (секрет уровня токена бота — не коммитить, не показывать);
- DNS привязан: `ksp.alikhandev.com` → туннель `ksp`.

Повторно `cloudflared tunnel login` / `tunnel create ksp` запускать не
нужно — команда один раз честно отказалась («already exists»), это
ожидаемо при повторном запуске, не поломка.

### Дальше — конфиг туннеля (вот чего не хватало)

`cloudflared` пока не знает, что после DNS-привязки трафик с
`ksp.alikhandev.com` нужно проксировать на `localhost:8000`.

**Важная поправка** (нашёл, проверив реальный файл на твоей машине, не
по памяти): конфиг для `sudo cloudflared service install` — **не**
`~/.cloudflared/config.yml`. Служба стартует от `root` через
`/Library/LaunchDaemons/com.cloudflare.cloudflared.plist`, который
запускает голый `cloudflared` без флага `--config`, а дефолт у него —
`/usr/local/etc/cloudflared/config.yml` (проверено: `cloudflared --help`
на твоём бинарнике прямо это показывает). Файл там уже есть — сама
`service install` создала его с одной строкой:

```
logDirectory: /var/log/cloudflared
```

Ни `tunnel`, ни `ingress` там нет — вот и весь `530`: Cloudflare
достучался до туннеля, а туннель не знает, что с трафиком делать
дальше. Путь системный (`/usr/local/etc/...`, владелец `root`) — нужен
`sudo`, обычный `cat >` без него упадёт с «Permission denied» (сама
запись идёт от твоего шелла, не от `sudo`, если просто написать `sudo
cat > файл` — это частая ловушка, тут нужен `sudo tee`):

```bash
sudo tee /usr/local/etc/cloudflared/config.yml > /dev/null <<'EOF'
logDirectory: /var/log/cloudflared
tunnel: ksp
credentials-file: /Users/kr220/.cloudflared/7aa529d1-e41e-4538-9e45-a2f2d08a2edc.json

ingress:
  - hostname: ksp.alikhandev.com
    service: http://localhost:8000
  - service: http_status:404
EOF
cat /usr/local/etc/cloudflared/config.yml
```

Последняя строка печатает файл обратно — проверь, что он выглядит так
же, как выше.

(`8000` — порт из `WEBAPP_PORT` в `.env`, Б0.3. Меняешь порт там —
поменяй и здесь, и в `launchd/com.alikhan.webapi.plist`.)

### Служба уже установлена — её нужно перезапустить

`sudo cloudflared service install` ты уже выполнил, служба
(`com.cloudflare.cloudflared`) в списке `launchctl` — но она стартовала
**до** того, как появился `config.yml`, поэтому не знала, куда
проксировать трафик. Это и есть причина `HTTP/2 530` в твоём curl —
Cloudflare достучался до твоего Mac, а сам `cloudflared` не знал, что
делать дальше. Перечитать конфиг:

```bash
sudo launchctl unload /Library/LaunchDaemons/com.cloudflare.cloudflared.plist
sudo launchctl load /Library/LaunchDaemons/com.cloudflare.cloudflared.plist
sudo launchctl list | grep cloudflared
```

### Второй кусок причины 530 — на 8000 порту ещё никто не слушает

`config.yml` теперь говорит «отправляй на `localhost:8000`», но сам
FastAPI-сервер (`web/api.py`, блок Б9) ещё не запущен как служба — я
этот агент упустил в первой версии блока, добавил сейчас:
`launchd/com.alikhan.webapi.plist`. Без него порт 8000 пустой в любом
случае, конфиг тут не помог бы. Установка — в разделе 2 ниже, файл уже
включён в общий список.

### Проверка после обоих шагов

```bash
curl -I https://ksp.alikhandev.com
```

Ожидается `HTTP/2 200` (или `401`, если случайно попал на `/api/...` —
это тоже означает, что всё работает, просто эндпоинт требует
`initData`). `530` означал бы, что один из двух шагов выше не выполнен.

### Что вписать после этого

- `.env`: `WEBAPP_URL=https://ksp.alikhandev.com`
- У `@BotFather`: `/mybots` → выбрать бота → `Bot Settings` → `Menu Button`
  (или там, где регистрируется Web App URL для кнопок) → вписать тот же
  адрес.

### КГ блока — судя по твоему логу, уже выполнен

Ты уже проверил это сам, реальной перезагрузкой (`sudo reboot`), и адрес
не изменился:

```
до перезагрузки:   HTTP/2 530   cf-ray: a30429d9acab8a3a-AKX
после перезагрузки: HTTP/2 530   cf-ray: a304316c1c425b0a-CPH
```

`cf-ray` разный (это нормально, он всегда разный на каждый отдельный
запрос — это ID конкретного запроса, не адреса), а вот сам домен
`ksp.alikhandev.com` — тот же самый до и после `reboot`. Это и есть КГ
блока Б11.1 («адрес пережил перезагрузку Mac и остался прежним») — уже
выполнен, статус 530 просто означал «туннель жив, а вот за ним пока
никто не слушает», не «адрес поменялся».

---

## 2. launchd-агенты

Пять файлов в этой папке:

| Файл | Что делает | Обязателен по Б11.2 |
|---|---|---|
| `com.alikhan.keepawake.plist` | Не даёт Mac уснуть | да |
| `com.alikhan.kspbot.plist` | Держит бота живым, автоперезапуск | да |
| `com.alikhan.webapi.plist` | FastAPI для Mini App на `localhost:8000` | добавлено по факту — без него Cloudflare Tunnel проксирует в пустоту (530), см. раздел 1 |
| `com.alikhan.watchdog.plist` | Запускает `scripts/watchdog.sh` раз в 5 минут | добавлено сверх списка Б11.2 — без расписания скрипт сам по себе ничего не проверяет |
| `com.alikhan.backup.plist` | Запускает `scripts/backup_db.sh` ежедневно в 03:00 | добавлено по той же причине |

### Ловушка: launchd не видит `PATH`

launchd запускает процессы не через login-шелл — `.zshrc`/`.bash_profile`
он не читает, значит и `PATH` оттуда не видит. Если в `ProgramArguments`
написать просто `python3`, агент **молча не запустится** (ни ошибки в
терминале, ни явного сообщения — только пустой `StandardErrorPath`, если
даже путь до него не найден). Поэтому во всех plist здесь — **абсолютные
пути**: `/Users/kr220/Documents/Projects/ksp-navigator/venv/bin/python3.11`,
не просто `python3`.

Второй слой той же ловушки — `com.alikhan.kspbot.plist` и
`com.alikhan.webapi.plist` запускают Python как `-m bot.main` /
`-m uvicorn web.api:app`, а не путём до файла напрямую. Дело в
`sys.path`: при запуске `python bot/main.py` Python подставляет в
`sys.path` папку `bot/`, а не корень проекта — `import core.config`
внутри `bot/main.py` тогда падает `ModuleNotFoundError: No module named
'core'`. Проверено вручную: `python bot/main.py` — падает, `python -m
bot.main` из `WorkingDirectory` (корень проекта) — нет.

### У тебя уже установлены keepawake, kspbot, watchdog, backup

Осталось добавить `webapi` — файл появился уже после того, как ты
ставил остальные четыре:

```bash
cp launchd/com.alikhan.webapi.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.alikhan.webapi.plist
launchctl list | grep com.alikhan
```

Если ставишь всё с нуля (например на другой машине):

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

### Проверка (КГ блока Б11.2)

`backup` и `watchdog` показывают `-` вместо PID, пока не наступило их
время по расписанию (`StartInterval`/`StartCalendarInterval`) — это не
ошибка, они не постоянные процессы. `keepawake`, `kspbot`, `webapi`
должны показывать настоящий PID постоянно:

```bash
launchctl list | grep com.alikhan
```

У тебя в логе `kspbot` уже показал PID дважды (2082, затем 2134 после
перезапуска) — бот реально работает прямо сейчас, с боевым токеном.
Стоит прямо сейчас открыть Telegram и написать своему боту `/start`,
чтобы увидеть это своими глазами, а не только по PID.

Перезапуск одного агента после правки кода:

```bash
launchctl unload ~/Library/LaunchAgents/com.alikhan.kspbot.plist
launchctl load ~/Library/LaunchAgents/com.alikhan.kspbot.plist
launchctl list | grep com.alikhan.kspbot
```

Если что-то не отвечает — смотреть логи:

```bash
tail -f ~/logs/kspbot.err.log
```

После перезагрузки Mac (`sudo reboot`) — бот должен отвечать на `/start`
сам, без ручного запуска (`RunAtLoad` + `KeepAlive` в plist). Ты это уже
проверил реальным `sudo reboot` для туннеля — тот же принцип и для бота.

### Системные настройки (руками, один раз — MASTER.md, п.1.4)

- System Settings → Battery → Power Adapter → «Prevent automatic sleeping
  when the display is off» — включить
- Крышку ноутбука не закрывать (или подключить внешний монитор для
  clamshell-режима)
- Отключить блокировку FileVault при простое — иначе процессы могут
  терять доступ к диску, пока экран заблокирован

---

## 3. Watchdog и бэкапы

- `scripts/watchdog.sh` — раз в 5 минут проверяет `api.telegram.org` и
  что `launchctl` видит агент бота. Три неудачи подряд → строка
  `ТРЕВОГА` в `~/logs/watchdog.log`.
- `scripts/backup_db.sh` — ежедневно в 03:00, `sqlite3 ... ".backup"`
  (не `cp` — база в режиме WAL, обычное копирование работающего файла
  может дать неполную или битую копию). Хранит 14 дней, старше — удаляет.
  Лог: `~/logs/backup.log`.

Найти тревогу в логе:

```bash
grep ТРЕВОГА ~/logs/watchdog.log
```

### Проверка бэкапа руками

```bash
bash scripts/backup_db.sh
ls -la backup/
```

Отдельно посмотреть, что в файле реально есть данные:

```bash
sqlite3 "backup/app_$(date +%Y-%m-%d).db" "SELECT COUNT(*) FROM curriculum_objectives;"
```
