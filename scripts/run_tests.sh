#!/usr/bin/env bash
# Запуск тестов на настоящем PostgreSQL 17 (для разработки, не для сервера).
# Поднимает временный контейнер uchet3-test-db на 127.0.0.1:55432, если его нет.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CONTAINER=uchet3-test-db

if ! docker info >/dev/null 2>&1; then
    echo "Docker не запущен." >&2
    exit 1
fi

if [ -z "$(docker ps -q -f name="^${CONTAINER}$")" ]; then
    docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
    docker run -d --name "$CONTAINER" -e POSTGRES_PASSWORD=postgres \
        -p 127.0.0.1:55432:5432 postgres:17 >/dev/null
fi
until docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; do sleep 1; done

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    python3.12 -m venv "$ROOT/.venv"
    "$ROOT/.venv/bin/pip" install -q -r "$ROOT/backend/requirements-dev.txt"
fi

cd "$ROOT/backend"
exec "$ROOT/.venv/bin/python" -m pytest "$@"
