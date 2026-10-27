#!/usr/bin/env bash
# Бэкап БД с ротацией (по умолчанию 14 дней).
#   Postgres → pg_dump | gzip  (pg_dump той же мажорной версии, что и сервер!)
#   SQLite   → консистентная копия через sqlite3.Connection.backup() + gzip
# Запуск: scripts/backup.sh   (DATABASE_URL из окружения или из .env в корне проекта)
# Переменные: BACKUP_DIR (по умолчанию ./backups), BACKUP_KEEP_DAYS (14).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

# Python проекта: .venv, иначе через uv
if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PY=("$ROOT/.venv/bin/python")
else
  PY=(uv run --project "$ROOT" python)
fi

# DATABASE_URL: из окружения, иначе одна строка из .env (без source — не исполняем файл)
if [[ -z "${DATABASE_URL:-}" && -f "$ROOT/.env" ]]; then
  DATABASE_URL="$(grep -E '^DATABASE_URL=' "$ROOT/.env" | tail -n1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//')"
fi
: "${DATABASE_URL:?DATABASE_URL не задан}"
export DATABASE_URL  # передаём в python через окружение, не через argv

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"
umask 077
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT=""
RAW=""
# убираем только временные файлы; готовый бэкап уже переименован из .tmp
cleanup() { [[ -n "$OUT" ]] && rm -f "$OUT.tmp"; [[ -n "$RAW" ]] && rm -f "$RAW"; return 0; }
trap cleanup ERR EXIT

case "$DATABASE_URL" in
  postgresql*|postgres*)
    # Разбираем URL в PG*-переменные: пароль уходит в PGPASSWORD, а не в argv
    # (argv виден всем в `ps`). Неизвестные query-параметры — ошибка, а не тихий игнор.
    if ! PGENV="$("${PY[@]}" - <<'PYEOF'
import os, shlex, sys
from urllib.parse import parse_qsl, unquote, urlsplit
u = urlsplit(os.environ["DATABASE_URL"])
env = {
    "PGHOST": u.hostname or "",
    "PGPORT": str(u.port or 5432),
    "PGUSER": unquote(u.username or ""),
    "PGPASSWORD": unquote(u.password or ""),
    "PGDATABASE": unquote(u.path.lstrip("/")),
}
# asyncpg-стиль ssl=… → libpq sslmode
SSL_MAP = {"true": "require", "1": "require", "false": "disable", "0": "disable"}
for key, value in parse_qsl(u.query, keep_blank_values=True):
    if key == "host":  # ?host=/run/postgresql — unix-сокет
        env["PGHOST"] = value
    elif key == "port":
        env["PGPORT"] = value
    elif key == "sslmode":
        env["PGSSLMODE"] = value
    elif key == "ssl":
        env["PGSSLMODE"] = SSL_MAP.get(value.lower(), value)
    else:
        sys.exit(f"backup: неподдерживаемый параметр DATABASE_URL: {key}")
if not env["PGDATABASE"]:
    sys.exit("backup: в DATABASE_URL нет имени базы")
env["PGHOST"] = env["PGHOST"] or "localhost"
for k, v in env.items():
    print(f"export {k}={shlex.quote(v)}")
PYEOF
)"; then
      echo "backup: не удалось разобрать DATABASE_URL" >&2
      exit 1
    fi
    eval "$PGENV"   # только export с shlex.quote, сгенерированные выше
    unset PGENV
    OUT="$BACKUP_DIR/surgut-place-$STAMP.sql.gz"
    pg_dump --no-owner --no-privileges | gzip -9 > "$OUT.tmp"
    mv "$OUT.tmp" "$OUT"
    unset PGPASSWORD
    ;;
  sqlite*)
    OUT="$BACKUP_DIR/surgut-place-$STAMP.sqlite3.gz"
    RAW="$BACKUP_DIR/.surgut-place-$STAMP.sqlite3.raw"
    BACKUP_OUT="$RAW" PROJECT_ROOT="$ROOT" "${PY[@]}" - <<'PYEOF'
import os, sqlite3, sys
from pathlib import Path
url = os.environ["DATABASE_URL"]
path = Path(url.split(":///", 1)[1].split("?", 1)[0])
if not path.is_absolute():
    path = Path(os.environ["PROJECT_ROOT"]) / path
if not path.is_file():
    sys.exit(f"SQLite-файл не найден: {path}")
src = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
dst = sqlite3.connect(os.environ["BACKUP_OUT"])
with dst:
    src.backup(dst)  # консистентно даже при работающем боте
dst.close()
src.close()
PYEOF
    gzip -9c "$RAW" > "$OUT.tmp"
    mv "$OUT.tmp" "$OUT"
    rm -f "$RAW"
    ;;
  *)
    echo "Неизвестная схема DATABASE_URL" >&2; exit 1 ;;
esac

# ротация: удаляем бэкапы старше KEEP_DAYS дней
find "$BACKUP_DIR" -maxdepth 1 -type f -name 'surgut-place-*' -mtime "+$KEEP_DAYS" -delete

echo "backup ok: $OUT ($(du -h "$OUT" | cut -f1))"
