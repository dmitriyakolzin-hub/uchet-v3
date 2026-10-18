#!/usr/bin/env bash
# Проверка, что бэкап восстанавливается. Рабочую базу не трогает:
# бэкап разворачивается во временную базу uchet3_restore_check, которая потом удаляется.
#
#   /opt/uchet-v3/deploy/backup_check.sh              — проверить последний бэкап
#   /opt/uchet-v3/deploy/backup_check.sh ФАЙЛ.dump    — проверить конкретный файл
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/opt/uchet-v3-backups}"
CHECK_DB=uchet3_restore_check

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

FILE="${1:-$(ls -1t "$BACKUP_DIR"/uchet3_*.dump 2>/dev/null | head -1 || true)}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    log "ОШИБКА: бэкап не найден"
    exit 1
fi

cd "$PROJECT_DIR"
psql_check() {
    docker compose exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $CHECK_DB -tA -c \"$1\""
}
cleanup() {
    docker compose exec -T db sh -c "dropdb -U \"\$POSTGRES_USER\" --if-exists --force $CHECK_DB" || true
}
trap cleanup EXIT

log "Проверяю бэкап $FILE"
cleanup 2>/dev/null
docker compose exec -T db sh -c "createdb -U \"\$POSTGRES_USER\" $CHECK_DB"
docker compose exec -T db sh -c \
    "pg_restore -U \"\$POSTGRES_USER\" -d $CHECK_DB --no-owner --exit-on-error" < "$FILE"

VERSION=$(psql_check "SELECT version_num FROM alembic_version")
TABLES=$(psql_check "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
USERS=$(psql_check "SELECT count(*) FROM users")

log "Версия схемы: $VERSION; таблиц: $TABLES; пользователей: $USERS"
if [ -z "$VERSION" ] || [ "$USERS" -lt 1 ]; then
    log "ПРОВЕРКА НЕ ПРОЙДЕНА: в бэкапе нет схемы или пользователей"
    exit 1
fi
log "ПРОВЕРКА ПРОЙДЕНА: бэкап восстанавливается"
