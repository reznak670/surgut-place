# Сургутский плейс

Telegram-бот с готовыми сценариями свиданий и досуга в Сургуте: каталог «на выходные», фильтры,
бронь слотов у партнёров, напоминания, отзывы, админка.

Стек: Python ≥3.11 (локально 3.14), aiogram 3, SQLAlchemy 2 async, Alembic, APScheduler 3, uv, ruff, pytest.
БД: SQLite — **только для разработки и тестов**. **В проде нужен PostgreSQL**: SQLite держит
одну блокировку записи на весь файл, и под нагрузкой бот начнёт ловить `database is locked`.
Redis — опционально, для FSM-состояний.

## Структура

```
app/
  main.py, __main__.py   точка входа (`python -m app`)
  config.py              настройки из .env → settings
  logging_setup.py       JSON-логи в stdout
  db/                    модели, движок, сессии
  services/              бизнес-логика (брони, поиск, отзывы, статистика, рассылки)
  scheduler/             напоминания, отзывы, дайджест, реактивация, алерты по заявкам
  middlewares/           сессия БД, пользователь, RoleFilter
  bot/                   хендлеры, клавиатуры, тексты (frontend)
migrations/              Alembic
scripts/seed.py          демо-сценарии
scripts/backup.sh        бэкап БД с ротацией
deploy/                  systemd-юниты, compose.yaml (Postgres + Redis)
tests/                   pytest
```

## Локальный запуск

```bash
cd ~/surgut-place
cp .env.example .env          # заполните BOT_TOKEN, ADMIN_IDS, ADMIN_CHAT_ID
uv sync                       # создаст .venv и поставит зависимости
uv run alembic upgrade head   # создаст схему БД
uv run python -m scripts.seed # 8 демо-сценариев + слоты на 2 ближайших выходных (можно запускать повторно)
uv run python -m app          # бот на long polling
```

Проверки:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
```

## Postgres и Redis через podman

Вариант 1 — compose (нужен `podman-compose` или `docker compose`):

```bash
POSTGRES_PASSWORD=секрет podman compose -f deploy/compose.yaml up -d
```

Вариант 2 — отдельные контейнеры:

```bash
podman run -d --name surgut-pg -p 127.0.0.1:5432:5432 \
  -e POSTGRES_DB=surgut_place -e POSTGRES_USER=surgut -e POSTGRES_PASSWORD=секрет \
  -v surgut-pg:/var/lib/postgresql/data docker.io/library/postgres:17-alpine
podman run -d --name surgut-redis -p 127.0.0.1:6379:6379 docker.io/library/redis:7-alpine
```

В `.env`:

```
DATABASE_URL=postgresql+asyncpg://surgut:секрет@127.0.0.1:5432/surgut_place
REDIS_URL=redis://127.0.0.1:6379/0
```

Затем `uv run alembic upgrade head` и `uv run python -m scripts.seed`.

## Деплой на VPS (systemd)

Юнит `deploy/surgut-place.service` — **системный** (system unit): бот работает от отдельного
пользователя `surgut` из `/opt/surgut-place` и перед стартом сам выполняет `alembic upgrade head`.
**В проде `DATABASE_URL` должен указывать на Postgres**, не на SQLite.

### 1. Postgres и Redis с автозапуском после ребута

Проще всего поставить пакеты дистрибутива (`dnf install postgresql-server redis` или
`apt install postgresql redis-server`) — они сами стартуют через systemd.

Если нужны контейнеры podman, сделайте их quadlet-юнитами, тогда systemd поднимет их после ребута:

```bash
sudo mkdir -p /etc/containers/systemd
sudo tee /etc/containers/systemd/surgut-pg.container >/dev/null <<'UNIT'
[Container]
Image=docker.io/library/postgres:17-alpine
Environment=POSTGRES_DB=surgut_place POSTGRES_USER=surgut POSTGRES_PASSWORD=секрет
PublishPort=127.0.0.1:5432:5432
Volume=surgut-pg:/var/lib/postgresql/data

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload && sudo systemctl start surgut-pg
```

Для Redis сделайте такой же файл `surgut-redis.container` (`Image=docker.io/library/redis:7-alpine`,
`PublishPort=127.0.0.1:6379:6379`).
Альтернатива — `podman run --restart=always ...` плюс `sudo systemctl enable podman-restart.service`.

### 2. Пользователь, код, зависимости

```bash
# системный пользователь без домашнего каталога (-M): каталог создадим сами
sudo useradd --system -M --home-dir /opt/surgut-place --shell /usr/sbin/nologin surgut

# код: клонируем во временную папку и переносим (git clone не умеет в существующий непустой каталог)
git clone <repo> /tmp/surgut-place
sudo mv /tmp/surgut-place /opt/surgut-place
sudo chown -R surgut:surgut /opt/surgut-place
sudo chmod 750 /opt/surgut-place

# uv — системно в /usr/local/bin, чтобы был доступен пользователю surgut
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

# .env: заполнить BOT_TOKEN, ADMIN_IDS, ADMIN_CHAT_ID, DATABASE_URL=postgresql+asyncpg://...
sudo -u surgut cp /opt/surgut-place/.env.example /opt/surgut-place/.env
sudo -u surgut chmod 600 /opt/surgut-place/.env
sudoedit /opt/surgut-place/.env

# зависимости, миграции, демо-данные — из каталога проекта (uv и alembic ищут файлы в cwd)
sudo -u surgut sh -c 'cd /opt/surgut-place && /usr/local/bin/uv sync --frozen --no-dev'
sudo -u surgut sh -c 'cd /opt/surgut-place && .venv/bin/alembic upgrade head'
sudo -u surgut sh -c 'cd /opt/surgut-place && .venv/bin/python -m scripts.seed'   # по желанию
sudo -u surgut mkdir -p -m 700 /opt/surgut-place/backups
```

### 3. systemd

```bash
sudo cp deploy/surgut-place.service deploy/surgut-place-backup.{service,timer} /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now surgut-place surgut-place-backup.timer

journalctl -u surgut-place -f        # логи (JSON)
```

Обновление:

```bash
sudo -u surgut sh -c 'cd /opt/surgut-place && git pull && /usr/local/bin/uv sync --frozen --no-dev'
sudo systemctl restart surgut-place      # миграции применит ExecStartPre
```

## Бэкапы

`scripts/backup.sh`:
- Postgres: `pg_dump | gzip`. Пароль передаётся через `PGPASSWORD` (переменная окружения процесса), а не через argv, поэтому его не видно в `ps`.
- SQLite: консистентная копия через `sqlite3.Connection.backup()` (Python из `.venv`) + gzip.

Файлы называются `backups/surgut-place-YYYYmmdd-HHMMSS.*.gz`; бэкапы старше 14 дней удаляются
(`BACKUP_DIR` и `BACKUP_KEEP_DAYS` переопределяются переменными окружения).
На VPS запускается таймером `surgut-place-backup.timer` ежедневно в 04:00.

**`pg_dump` должен быть той же мажорной версии, что и сервер** (или новее). Более старый `pg_dump`
откажется работать с новым сервером. Если Postgres крутится в контейнере `postgres:17`, на хост
ставьте клиент 17-й версии (`dnf install postgresql17` / пакет из репозитория PGDG).

Восстановление Postgres:
`gunzip -c backups/<файл>.sql.gz | PGPASSWORD=… psql -h 127.0.0.1 -U surgut surgut_place`.

`DATABASE_URL` для бэкапа может содержать параметры `?host=…` (unix-сокет), `?sslmode=…` и `?ssl=…`;
любой другой параметр — ошибка, и скрипт останавливается до `pg_dump`.

## Переменные .env

| Переменная | Обязательна | Описание |
|---|---|---|
| `BOT_TOKEN` | да | токен от @BotFather |
| `ADMIN_IDS` | да | tg_id админов через запятую (роль admin выдаётся автоматически) |
| `ADMIN_CHAT_ID` | да | чат для алертов: низкие оценки, «Предложить место», брони без партнёра, заявки >2 ч |
| `DATABASE_URL` | да | dev: `sqlite+aiosqlite:///./dev.db`; **прод: только `postgresql+asyncpg://…`** |
| `REDIS_URL` | нет | пусто → MemoryStorage (FSM теряется при рестарте) |
| `PHOTO_STORAGE` | нет | `telegram` (file_id / URL) |
| `S3_ENDPOINT`, `S3_BUCKET`, `S3_ACCESS_KEY`, `S3_SECRET_KEY` | нет | заготовка, пока не используется |
| `TZ` | нет | `Asia/Yekaterinburg` |
| `PRIVACY_POLICY_URL` | да | ссылка на политику обработки ПД (152-ФЗ) |
| `LOG_LEVEL` | нет | `INFO` / `DEBUG` |
| `FROST_MODE` | нет | режим «мороз»: `1`/`true`/`yes` — включён, по умолчанию выключен |
| `ASSETS_DIR` | нет | папка со статикой бота, по умолчанию `<корень проекта>/assets` |

## Фоновые задачи

| Когда | Что |
|---|---|
| каждые 5 мин | напоминания за 24 ч и за 3 ч до подтверждённой брони |
| каждые 10 мин | запрос отзыва через 2 ч после окончания; алерт админу по заявкам в pending > 2 ч |
| чт 18:00 | дайджест сценариев на выходные активным пользователям (если сценариев нет — не шлём) |
| ежедневно 12:00 | одно сообщение-реактивация тем, кто не заходил 30+ дней |
