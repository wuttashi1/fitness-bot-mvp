# Запуск на Linux-сервере с CasaOS

Проект запускается в одном Docker-контейнере. База данных и резервные копии находятся на диске сервера, отдельно от образа. Обновление контейнера сохраняет данные. Порты, домен, HTTPS и reverse proxy для бота не нужны: используется исходящее соединение с Telegram.

Нужны 64-битный Linux, Docker, Docker Compose v2, доступ к серверу по SSH и токен от @BotFather. Образ собирается непосредственно на сервере под его архитектуру; в Dockerfile не зафиксирован `amd64`. ARM64 предусмотрен, но отдельно не проверен. Интернет нужен для загрузки образа Python/пакетов и работы Telegram.

**Выберите один способ управления:** A — Docker Compose по SSH; B — приложение в CasaOS. Подготовка одинакова. Не запускайте два экземпляра с одним токеном.

## 1. Загрузить проект

Загрузите `fitness-bot-mvp.zip` на сервер как `/tmp/fitness-bot-mvp.zip` через SFTP или файловый менеджер CasaOS. По SSH выполните:

```bash
sudo mkdir -p /DATA/AppData
sudo unzip /tmp/fitness-bot-mvp.zip -d /DATA/AppData
cd /DATA/AppData/fitness-bot
sudo docker version
sudo docker compose version
```

После распаковки `Dockerfile`, `compose.yaml` и `.env.example` должны лежать прямо в `/DATA/AppData/fitness-bot`. Если распаковываете через интерфейс, убедитесь, что нет лишней вложенной папки `fitness-bot/fitness-bot`.

Если `docker compose` не найден, установите Compose plugin по [официальной инструкции Docker](https://docs.docker.com/compose/install/linux/). Если отсутствует только `unzip`, установите его пакетным менеджером своего Linux. Python на хосте устанавливать не нужно.

## 2. Настроить токен, владельца и папки данных

При первой установке:

```bash
cd /DATA/AppData/fitness-bot
sudo cp -n .env.example .env
sudo nano .env
```

Обязательные значения:

```dotenv
BOT_TOKEN=ваш_токен_из_BotFather
OWNER_TELEGRAM_ID=ваш_числовой_Telegram_ID
OWNER_DISPLAY_NAME=
DEFAULT_TIMEZONE=Europe/Berlin
CHECKIN_HOUR=10
POLL_INTERVAL_SECONDS=5
```

`OWNER_TELEGRAM_ID` — ID вашего личного Telegram-аккаунта, **не ID бота из токена**. `OWNER_DISPLAY_NAME` можно оставить пустым: имя и @username авторства берутся из вашего Telegram при обращении к боту. Имя пользователя вручную в это поле вписывать не требуется.

Сохраните `.env`: Ctrl+O, Enter, Ctrl+X. Затем:

```bash
sudo install -d -o 10001 -g 10001 -m 750 /DATA/AppData/fitness-bot/data
sudo install -d -o 10001 -g 10001 -m 750 /DATA/AppData/fitness-bot/backups
sudo chown 10001:10001 /DATA/AppData/fitness-bot/.env
sudo chmod 600 /DATA/AppData/fitness-bot/.env
```

Контейнер работает от пользователя UID/GID `10001:10001`. Эти права позволяют ему писать БД и читать `.env` при установке через CasaOS. Токен не включается в образ; файл `.env` исключён из контекста сборки. Не публикуйте `.env` и вывод команды `docker compose config` без `--quiet`, поскольку он может содержать токен.

Внутри контейнера БД всегда `/data/fitness.sqlite3`. Значение `DATABASE_PATH` из старого `.env` перекрывается настройкой Compose, поэтому менять его для Docker необязательно.

## 3. Собрать образ

```bash
cd /DATA/AppData/fitness-bot
sudo docker compose build
```

Сборка создаст локальный образ `fitness-telegram-bot:0.2.0`. Его нет в публичном реестре: CasaOS использует образ, уже собранный на этом сервере.

Если ваш Telegram ID неизвестен, пока заполните только токен и запустите:

```bash
sudo docker compose run --rm --no-deps bot --identify
```

Отправьте `/id` своему боту в Telegram. Впишите полученный числовой ID в `.env`, остановите режим определения через Ctrl+C. Другие экземпляры бота в это время должны быть остановлены.

Проверка БД без отправки сообщений Telegram:

```bash
sudo docker compose config --quiet
sudo docker compose run --rm --no-deps bot --init-db
```

Ожидаемый результат: `Database and Excel starter seed initialized.` Повторная команда не дублирует seed. Если БД уже содержит данные предыдущей версии, миграция выполняется автоматически.

## Способ A. Запуск по SSH через Docker Compose

```bash
cd /DATA/AppData/fitness-bot
sudo docker compose up -d --no-build
sudo docker compose ps
sudo docker compose logs --tail=100 -f bot
```

Ctrl+C выходит из просмотра логов, контейнер продолжает работу. Откройте бота в Telegram → `/start`. Автор основной программы привяжется к аккаунту, указанному в `OWNER_TELEGRAM_ID`.

Контейнер будет работать на сервере CasaOS и перезапускаться после перезагрузки благодаря `restart: unless-stopped`, если Docker запускается вместе с системой. После ручной остановки запустите его снова командой `up -d`. Поведение политики описано в [документации Docker](https://docs.docker.com/engine/containers/start-containers-automatically/).

Этот способ не требует, чтобы приложение отображалось отдельной плиткой CasaOS. Если нужна установка именно через интерфейс CasaOS, используйте способ B вместо A.

## Способ B. Установка через интерфейс CasaOS

Сначала выполните общую подготовку и сборку образа из разделов 1–3. **Не запускайте `compose up` из способа A.**

1. Откройте CasaOS в браузере.
2. Откройте установку пользовательского приложения: обычно **«+» → Install a customized app / Custom Install**. Названия пунктов могут отличаться между версиями.
3. Выберите **Import → Docker Compose** и вставьте содержимое `compose.casaos.yaml`.
4. Проверьте образ: `fitness-telegram-bot:0.2.0`, имя контейнера: `fitness-bot`.
5. Проверьте три привязки папок/файла:
   - `/DATA/AppData/fitness-bot/.env` → `/app/.env`, только чтение;
   - `/DATA/AppData/fitness-bot/data` → `/data`;
   - `/DATA/AppData/fitness-bot/backups` → `/backups`.
6. Оставьте порты пустыми. У бота нет веб-интерфейса. Он используется через Telegram.
7. Установите приложение и проверьте журнал контейнера. Затем отправьте `/start` боту.

В файле CasaOS используются абсолютные пути: после импорта CasaOS может хранить свой Compose в другой папке. Конфигурация монтируется как файл, чтобы не зависеть от обработки относительного `env_file` в импортёре.

`pull_policy: never` указывает использовать локальный образ. Если ваша версия CasaOS всё равно пытается скачать образ и показывает `pull access denied`, не подставляйте случайный образ из реестра: используйте способ A. Совместимость конкретного импортёра CasaOS с локальными образами здесь не проверялась. Файл предназначен для пользовательской установки, не для публикации в App Store; фиктивный веб-порт и URL иконки в нём не добавлены.

Механизм пользовательского импорта показан в [репозитории CasaOS](https://github.com/IceWhaleTech/CasaOS/discussions/1491); разделение Compose и метаданных — в [спецификации CasaOS](https://github.com/IceWhaleTech/CasaOS-AppStore/blob/main/docs/specs/compose-and-x-casaos.md).

Если уже запущен способ A и хотите перейти к B, сначала выполните `sudo docker compose down` в папке проекта. Эта команда удаляет контейнер, но не наши папки с данными. Затем импортируйте файл CasaOS. Не выбирайте удаление пользовательских данных в интерфейсе CasaOS.

## Резервное копирование

Для любого способа установки:

```bash
sudo docker exec fitness-bot python /app/scripts/backup.py \
  /data/fitness.sqlite3 "/backups/fitness-$(date +%Y%m%d-%H%M%S).sqlite3"
```

Готовая копия появится в `/DATA/AppData/fitness-bot/backups`. Копируйте резервные файлы также на другой диск или устройство. Скрипт использует SQLite backup API и учитывает WAL; бот может продолжать работу. При совпадении имени скрипт откажется перезаписывать файл.

## Перенос уже существующей базы

Остановите старый экземпляр бота. Подготовьте резервную копию его БД скриптом `scripts/backup.py`. При WAL нельзя переносить только основной файл работающей БД обычным копированием.

До первого запуска нового контейнера поместите резервную копию как:

```text
/DATA/AppData/fitness-bot/data/fitness.sqlite3
```

Назначьте права:

```bash
sudo chown 10001:10001 /DATA/AppData/fitness-bot/data/fitness.sqlite3
sudo chmod 600 /DATA/AppData/fitness-bot/data/fitness.sqlite3
```

`OWNER_TELEGRAM_ID` должен совпадать с владельцем старой БД. Не смешивайте переносимую копию с файлами `-wal`/`-shm` другой базы. Если на сервере БД уже была запущена, используйте восстановление через новую папку ниже.

## Обновление кода

### Если используется способ A

1. Сделайте резервную копию командой выше.
2. Загрузите новый архив. Распакуйте код поверх проекта, сохранив `.env`, `data/` и `backups/` — они не включаются в поставляемый ZIP.
3. Выполните:

```bash
cd /DATA/AppData/fitness-bot
sudo docker compose build --pull
sudo docker compose up -d --no-build --force-recreate
sudo docker compose logs --tail=100 bot
```

### Если используется способ B

После резервного копирования и замены кода пересоберите образ через `sudo docker compose build --pull`. Затем пересоздайте приложение через CasaOS с актуальным `compose.casaos.yaml`, сохранив привязки данных. Простой Restart не переключает существующий контейнер на пересобранный образ. Если версия CasaOS не позволяет пересоздание с локальным образом, переходите на способ A после остановки и удаления только контейнера в CasaOS.

Не используйте автоматическое обновление из реестра для локального образа. При изменении версии образа обновляйте оба Compose-файла из поставки.

## Восстановление резервной копии

Остановите контейнер. Создайте **новую пустую** папку, например `/DATA/AppData/fitness-bot/data-restored-20260907`, назначьте UID/GID 10001 и скопируйте туда выбранный бэкап под именем `fitness.sqlite3`. Назначьте файлу владельца 10001:10001. Измените исходный путь привязки `/data` в активном Compose/CasaOS на новую папку и пересоздайте контейнер. Старая папка останется для возврата, а WAL/SHM прежней базы не смешаются с копией.

## Частые проблемы

- **Permission denied / unable to open database** — проверьте владельца папки данных и всех перенесённых файлов: UID/GID 10001. Не меняйте права всего `/DATA`.
- **Conflict: terminated by other getUpdates request** — с тем же токеном запущен второй бот. Остановите прежний процесс на ПК/сервере или режим `--identify`.
- **Unauthorized / token invalid** — исправьте `BOT_TOKEN`. Для способа A после изменения `.env` выполните `docker compose up -d --force-recreate`. Для способа B достаточно перезапуска, если изменилась только смонтированная `.env`.
- **Webhook is active** — токен ранее использовался с webhook. Удалите прежний webhook через Telegram Bot API или используйте новый токен; polling не запускается одновременно с webhook.
- **Set YOUR numeric OWNER_TELEGRAM_ID** — заполните ID личного аккаунта, без `@`.
- **OWNER_TELEGRAM_ID differs from existing seed owner** — подключена существующая БД другого владельца; верните её исходный ID. Подмена владельца автоматически не выполняется.
- **pull access denied** — локальный образ не собран либо импортёр пытается скачать его. Выполните сборку на этом же сервере; при необходимости используйте способ A.
- **Бот не отвечает** — проверьте логи, DNS и исходящий HTTPS к Telegram. Публикация портов не нужна.

## Что проверено в поставке

Конфигурационные файлы и содержимое архива проверены локально. 31 тест приложения прошёл, включая чтение `.env` из рабочей папки при запуске установленного пакета. Docker в среде разработки отсутствует: сборка образа, запуск Linux-контейнера и импорт в реальный CasaOS не выполнялись. Команды `docker compose config --quiet`, `build` и `run --init-db` выше предназначены для проверки на вашем сервере перед рабочим запуском.
