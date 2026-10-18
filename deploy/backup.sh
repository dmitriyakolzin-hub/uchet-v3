#!/usr/bin/env bash
# Бэкап базы новой системы (uchet-v3). Старую систему не трогает.
#
#   /opt/uchet-v3/deploy/backup.sh
#
# Файлы: /opt/uchet-v3-backups/uchet3_ГГГГ-ММ-ДД_ЧЧММСС.dump, хранятся 14 дней.
# Папку и срок можно поменять: BACKUP_DIR=... BACKUP_KEEP_DAYS=... backup.sh
# BACKUP_LABEL=... добавляет метку в имя файла (например, before-restore).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/opt/uchet-v3-backups}"
KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"

log() { echo "$(date '+%Y-%m-%d %H:%M:%S') $*"; }

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

LABEL="${BACKUP_LABEL:+${BACKUP_LABEL}_}"
FILE="$BACKUP_DIR/uchet3_${LABEL}$(date +%Y-%m-%d_%H%M%S).dump"
if [ -e "$FILE" ]; then
    log "ОШИБКА: файл $FILE уже существует, не перезаписываю"
    exit 1
fi
TMP="$FILE.part"
trap 'rm -f "$TMP"' EXIT

cd "$PROJECT_DIR"
log "Начинаю бэкап базы в $FILE"
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom' > "$TMP"

# Убеждаемся, что архив читается целиком
if ! docker compose exec -T db pg_restore --list < "$TMP" > /dev/null; then
    log "ОШИБКА: архив повреждён, бэкап не сохранён"
    exit 1
fi

mv "$TMP" "$FILE"
chmod 600 "$FILE"
log "Бэкап готов: $FILE ($(du -h "$FILE" | cut -f1))"

DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -name 'uchet3_*.dump' -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [ "$DELETED" -gt 0 ]; then
    log "Удалено старых бэкапов (старше $KEEP_DAYS дн.): $DELETED"
fi
