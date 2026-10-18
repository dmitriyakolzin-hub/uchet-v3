#!/usr/bin/env bash
# Восстановление базы новой системы (uchet-v3) из бэкапа.
# ВНИМАНИЕ: текущие данные базы uchet-v3 будут заменены данными из бэкапа.
# Перед восстановлением автоматически делается страховочный бэкап текущей базы.
#
#   /opt/uchet-v3/deploy/restore.sh /opt/uchet-v3-backups/uchet3_2026-10-18_031500.dump
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
    echo "Укажите файл бэкапа. Имеющиеся бэкапы:"
    ls -1t "${BACKUP_DIR:-/opt/uchet-v3-backups}"/uchet3_*.dump 2>/dev/null | head -20 || echo "  (нет)"
    exit 1
fi
FILE="$(cd "$(dirname "$FILE")" && pwd)/$(basename "$FILE")"

cd "$PROJECT_DIR"

if [ "${2:-}" != "--yes" ]; then
    echo "База новой системы будет заменена данными из:"
    echo "  $FILE"
    read -r -p "Для продолжения введите ДА: " ANSWER
    if [ "$ANSWER" != "ДА" ]; then
        echo "Отменено."
        exit 1
    fi
fi

log "Проверяю архив"
docker compose exec -T db pg_restore --list < "$FILE" > /dev/null

log "Страховочный бэкап текущей базы"
BACKUP_LABEL=before-restore "$PROJECT_DIR/deploy/backup.sh"

log "Останавливаю приложение"
docker compose stop app

log "Пересоздаю базу и загружаю бэкап"
docker compose exec -T db sh -c \
    'dropdb -U "$POSTGRES_USER" --if-exists --force "$POSTGRES_DB" && createdb -U "$POSTGRES_USER" "$POSTGRES_DB"'
docker compose exec -T db sh -c \
    'pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --exit-on-error' < "$FILE"

log "Запускаю приложение"
docker compose start app

for _ in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${APP_PORT:-8001}/api/health" > /dev/null 2>&1; then
        log "Готово: база восстановлена, приложение отвечает"
        exit 0
    fi
    sleep 2
done
log "ОШИБКА: приложение не отвечает после восстановления. Пришлите вывод: docker compose logs --tail 50 app"
exit 1
