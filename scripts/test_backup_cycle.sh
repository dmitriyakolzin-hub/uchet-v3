#!/usr/bin/env bash
# Проверка полного цикла бэкап → изменение данных → восстановление на запущенном
# docker compose (для разработки). Требует запущенный стек и созданного пользователя admin.
# Бэкапы пишутся во временную папку, рабочая папка бэкапов не используется.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export BACKUP_DIR="$(mktemp -d)"
trap 'rm -rf "$BACKUP_DIR"' EXIT

fail() { echo "ТЕСТ НЕ ПРОЙДЕН: $*" >&2; exit 1; }
sql() { docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -qtA -v ON_ERROR_STOP=1' <<< "$1"; }
logins() { sql "SELECT string_agg(login, ',' ORDER BY login) FROM users"; }

BEFORE="$(logins)"
[ -n "$BEFORE" ] || fail "в базе нет пользователей"

./deploy/backup.sh
FILE="$(ls -1t "$BACKUP_DIR"/uchet3_*.dump | head -1)"

sql "INSERT INTO users (login, password_hash, full_name) VALUES ('cycle-test', 'x', '')"
[ "$(logins)" != "$BEFORE" ] || fail "тестовый пользователь не добавился"

./deploy/backup_check.sh "$FILE"

echo "нет" | ./deploy/restore.sh "$FILE" && fail "восстановление прошло без подтверждения"
[ "$(logins)" != "$BEFORE" ] || fail "отказ от восстановления изменил базу"

echo "ДА" | ./deploy/restore.sh "$FILE"
[ "$(logins)" = "$BEFORE" ] || fail "после восстановления пользователи: $(logins), ожидалось: $BEFORE"
ls "$BACKUP_DIR"/uchet3_before-restore_*.dump > /dev/null || fail "нет страховочного бэкапа"
[ -f "$FILE" ] || fail "исходный бэкап пропал"

echo "ТЕСТ ПРОЙДЕН: бэкап восстанавливается, данные вернулись к состоянию на момент бэкапа"
