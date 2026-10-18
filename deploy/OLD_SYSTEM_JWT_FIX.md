# Старая система: закрыть дыру с JWT_SECRET

**Проблема.** В контейнер `uchet_backend` старой системы (`/opt/uchet-app`, `cfo-avmv.ru`)
не передаётся `JWT_SECRET`, поэтому токены подписываются значением по умолчанию из исходного кода
(`backend/core/config.py`). Любой, кто видел этот код, может подделать токен и войти под кем угодно.

**Что делаем.** Кладём случайный секрет в `/opt/uchet-app/.env` и передаём его в контейнер
через отдельный файл `compose.override.yaml` (Docker Compose подхватывает его сам).
Файлы `compose.yaml` и код старой системы **не меняются**, база не перезапускается.

**Последствия.** Все пользователи старой системы будут разлогинены и войдут заново
со своими паролями (согласовано). Сайт недоступен 5–15 секунд, пока перезапускается `uchet_backend`.

Проверено по данным с сервера: токены создаются и проверяются только в `backend/core/security.py`
через `settings.JWT_SECRET`; `backend/auth.py` секрета не содержит.

---

## Шаг 1. Проверка и страховочные копии

```bash
cd /opt/uchet-app
ls compose.override.yaml compose.override.yml docker-compose.override.yml 2>/dev/null && echo "СТОП: override-файл уже есть, пришлите вывод в сессию" || echo "override-файла нет, можно продолжать"
cp -p .env .env.bak-$(date +%F) && cp -p compose.yaml compose.yaml.bak-$(date +%F) && echo "копии сделаны"
docker exec uchet_backend sh -c '[ -n "$JWT_SECRET" ] && echo "JWT_SECRET: ЗАДАН" || echo "JWT_SECRET: НЕ ЗАДАН"'
```

**Должно вывести:**
```
override-файла нет, можно продолжать
копии сделаны
JWT_SECRET: НЕ ЗАДАН
```

## Шаг 2. Записать секрет в .env

```bash
cd /opt/uchet-app
[ -n "$(tail -c1 .env)" ] && echo >> .env
grep -q '^JWT_SECRET=' .env || echo "JWT_SECRET=$(openssl rand -hex 32)" >> .env
grep -o -E '^[A-Z_]+' .env | tr '\n' ' '; echo
grep '^JWT_SECRET=' .env | awk -F= '{print "длина секрета:", length($2)}'
```

**Должно вывести:**
```
POSTGRES_PASSWORD JWT_SECRET
длина секрета: 64
```

## Шаг 3. Передать секрет в контейнер

```bash
cd /opt/uchet-app
cat > compose.override.yaml <<'EOF'
# Передаёт JWT_SECRET из .env в контейнер старой системы (см. uchet-v3/deploy/OLD_SYSTEM_JWT_FIX.md)
services:
  backend:
    environment:
      JWT_SECRET: ${JWT_SECRET:?JWT_SECRET не задан в .env}
EOF
docker compose config --quiet && echo "настройки в порядке"
```

**Должно вывести:** `настройки в порядке`. Если вместо этого ошибка — **не продолжайте**,
удалите файл командой `rm /opt/uchet-app/compose.override.yaml` и пришлите ошибку в сессию.

## Шаг 4. Перезапустить только uchet_backend

```bash
cd /opt/uchet-app
docker compose up -d --no-deps backend
sleep 10
docker compose ps --format '{{.Name}}  {{.Status}}'
docker exec uchet_backend sh -c '[ -n "$JWT_SECRET" ] && echo "JWT_SECRET: ЗАДАН (${#JWT_SECRET} символов)" || echo "JWT_SECRET: НЕ ЗАДАН"'
```

**Должно вывести:** оба контейнера (`uchet_backend`, `uchet_postgres`) в состоянии `Up`
и `JWT_SECRET: ЗАДАН (64 символов)`. У `uchet_postgres` время работы прежнее (база не перезапускалась).

## Шаг 5. Проверка

1. Приложение использует новый секрет, а не значение по умолчанию:

```bash
docker exec uchet_backend python -c "
import os
from core.config import settings
print('используется секрет из .env:', settings.JWT_SECRET == os.environ['JWT_SECRET'])
"
```

**Должно вывести:** `используется секрет из .env: True`. Если вместо этого ошибка
(`ModuleNotFoundError`, `ImportError`) — это не поломка, просто пришлите её в сессию.

2. Откройте https://cfo-avmv.ru в браузере. Система попросит войти заново — войдите со своим
паролем, всё должно работать как раньше.

---

## Если что-то пошло не так — откат

Возвращает всё как было (секрет снова перестаёт действовать):

```bash
cd /opt/uchet-app
rm compose.override.yaml
docker compose up -d --no-deps backend
sleep 10
docker compose ps --format '{{.Name}}  {{.Status}}'
```

**Должно вывести:** оба контейнера `Up`. Пришлите в сессию вывод шага, на котором возникла
проблема, и `docker compose logs --tail 50 backend`.

> Строку `JWT_SECRET=…` в `.env` и копии `*.bak-*` удалять не нужно — они ничему не мешают.
