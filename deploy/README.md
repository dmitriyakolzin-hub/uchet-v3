# Выкладка новой системы (uchet-v3) на сервер

Все команды выполняются на сервере под `root`. Копируйте каждый блок целиком.
После каждого раздела есть **проверка**: если вывод не совпадает с ожидаемым, остановитесь
и пришлите в сессию команду, которую выполняли, и её вывод.

Старая система (`/opt/uchet-app`, `cfo-avmv.ru`, контейнеры `uchet_backend`, `uchet_postgres`,
порт 8000) этими инструкциями **не затрагивается**.
Отдельная инструкция для старой системы — [OLD_SYSTEM_JWT_FIX.md](OLD_SYSTEM_JWT_FIX.md)
(закрыть дыру с `JWT_SECRET`).

| Что | Новая система |
|---|---|
| Папка | `/opt/uchet-v3` |
| Бэкапы | `/opt/uchet-v3-backups` |
| Контейнеры | `uchet3-app`, `uchet3-db` |
| Том базы | `uchet3_pgdata` |
| Порт | `127.0.0.1:8001` (снаружи — только через nginx) |
| Домен | `new.cfo-avmv.ru` |

Содержание:
1. [Первая установка](#1-первая-установка)
2. [Обновление](#2-обновление)
3. [Откат на предыдущую версию](#3-откат-на-предыдущую-версию)
4. [Бэкап и восстановление](#4-бэкап-и-восстановление)
5. [Если что-то пошло не так](#5-если-что-то-пошло-не-так)

---

## 1. Первая установка

### 1.1. Проверка перед установкой

```bash
ss -ltn | grep -q ':8001 ' && echo "порт 8001 ЗАНЯТ" || echo "порт 8001 свободен"
test -e /opt/uchet-v3 && echo "папка /opt/uchet-v3 УЖЕ ЕСТЬ" || echo "папки нет"
docker volume ls -q | grep -q '^uchet3_' && echo "том базы УЖЕ ЕСТЬ" || echo "тома нет"
```

**Должно вывести:**
```
порт 8001 свободен
папки нет
тома нет
```

Проверка домена:

```bash
echo "Сервер: $(curl -s4 https://ifconfig.me)  Домен: $(getent ahostsv4 new.cfo-avmv.ru | awk 'NR==1{print $1}')"
```

**Должно вывести** два одинаковых IP-адреса. Если после «Домен:» пусто или адрес другой —
в панели reg.ru добавьте для `cfo-avmv.ru` запись `A`, имя `new`, значение — IP сервера,
и подождите 15–60 минут. Без этого не получится шаг 1.7 (HTTPS), остальные шаги можно делать.

### 1.2. Скачать код

```bash
git clone https://github.com/dmitriyakolzin-hub/uchet-v3.git /opt/uchet-v3
cd /opt/uchet-v3 && git log -1 --oneline
```

**Должно вывести** одну строку: код коммита и его описание.

### 1.3. Создать файл с секретами `.env`

Команда сама сгенерирует случайный пароль базы и секрет токенов:

```bash
cd /opt/uchet-v3
cp .env.example .env
chmod 600 .env
sed -i "s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 24)/" .env
sed -i "s/^JWT_SECRET=.*/JWT_SECRET=$(openssl rand -hex 32)/" .env
grep -E '^(POSTGRES_PASSWORD|JWT_SECRET)=' .env | awk -F= '{print $1, length($2), "символов"}'
```

**Должно вывести:**
```
POSTGRES_PASSWORD 48 символов
JWT_SECRET 64 символов
```

Файл `.env` — единственное место, где хранятся секреты. В git он не попадает. Не пересылайте
его содержимое никому (в том числе в сессию).

### 1.4. Запустить

```bash
cd /opt/uchet-v3
docker compose up -d --build
sleep 15
docker compose ps --format '{{.Name}}  {{.Status}}'
```

Первая сборка занимает 1–3 минуты. **Должно вывести** (через 15–30 секунд после запуска):
```
uchet3-app  Up ... (healthy)
uchet3-db  Up ... (healthy)
```
Если у `uchet3-app` написано `(health: starting)` — подождите 20 секунд и повторите последнюю команду.

### 1.5. Создать таблицы в базе (миграции)

```bash
cd /opt/uchet-v3
docker compose exec app alembic upgrade head
docker compose exec app alembic current
```

**Должно вывести** в конце: `0001 (head)`.

### 1.6. Создать первого администратора

Вместо `admin` можно указать свой логин, вместо «Администратор» — ФИО. Пароль спросят
два раза (при вводе символы не отображаются), не короче 10 символов.

```bash
cd /opt/uchet-v3
docker compose exec app python -m app.cli create-admin admin --name "Администратор"
```

**Должно вывести:** `Администратор «admin» создан.`

Проверка, что приложение отвечает:

```bash
curl -s http://127.0.0.1:8001/api/health
```

**Должно вывести:** `{"status":"ok","db":"ok"}`

### 1.7. Nginx и HTTPS для new.cfo-avmv.ru

Добавляем **отдельный** файл для нового домена (файл `default` со старым сайтом не меняется):

```bash
cp /opt/uchet-v3/deploy/nginx/new.cfo-avmv.ru.conf /etc/nginx/sites-available/new.cfo-avmv.ru
ln -s /etc/nginx/sites-available/new.cfo-avmv.ru /etc/nginx/sites-enabled/new.cfo-avmv.ru
nginx -t && systemctl reload nginx
curl -s -H 'Host: new.cfo-avmv.ru' http://127.0.0.1/api/health
```

**Должно вывести:** `syntax is ok`, `test is successful` и `{"status":"ok","db":"ok"}`.
Если `nginx -t` выдал ошибку — nginx **не** перезагружался, старый сайт работает как раньше;
удалите ссылку командой `rm /etc/nginx/sites-enabled/new.cfo-avmv.ru` и пришлите ошибку.

Сертификат HTTPS (нужна запись домена из шага 1.1):

```bash
certbot --nginx -d new.cfo-avmv.ru --redirect
```

certbot спросит e-mail (если ещё не спрашивал) и согласие с условиями. Он изменит только файл
`new.cfo-avmv.ru`. Проверка:

```bash
curl -s https://new.cfo-avmv.ru/api/health ; echo ; curl -s -o /dev/null -w 'Старый сайт: %{http_code}\n' https://cfo-avmv.ru/
```

**Должно вывести:** `{"status":"ok","db":"ok"}` и `Старый сайт: 200` (или `301`/`302`) — старый сайт продолжает работать.

Если certbot написал `NXDOMAIN` — запись домена `new` ещё не появилась (шаг 1.1). Ничего не сломалось:
certbot сам отменяет свои временные изменения. Дождитесь, пока проверка из шага 1.1 покажет IP сервера,
и запустите certbot ещё раз.

Теперь откройте в браузере https://new.cfo-avmv.ru и войдите под администратором.
Должно появиться: «Вы вошли как Администратор».

### 1.8. Включить ежедневный бэкап

```bash
mkdir -p /opt/uchet-v3-backups && chmod 700 /opt/uchet-v3-backups
install -m 644 /opt/uchet-v3/deploy/uchet3-backup.cron /etc/cron.d/uchet3-backup
/opt/uchet-v3/deploy/backup.sh && /opt/uchet-v3/deploy/backup_check.sh
```

**Должно вывести** в конце: `ПРОВЕРКА ПРОЙДЕНА: бэкап восстанавливается`.

Дальше бэкап делается сам каждый день в 03:15 (по времени сервера), бэкапы старше 14 дней
удаляются, по воскресеньям в 04:15 последний бэкап проверяется. Журнал — `/var/log/uchet3-backup.log`.

---

## 2. Обновление

Когда в сессии сказали «готово к выкладке» (изменения влиты в ветку `main`):

```bash
cd /opt/uchet-v3
git log -1 --oneline                      # запишите: это текущая версия, к ней можно откатиться
BACKUP_LABEL=before-update ./deploy/backup.sh
git checkout main && git pull
docker compose up -d --build
docker compose exec app alembic upgrade head
sleep 10
curl -s http://127.0.0.1:8001/api/health ; echo ; git log -1 --oneline
```

**Должно вывести:** строку «Бэкап готов», затем `{"status":"ok","db":"ok"}` и новую версию
(код коммита отличается от записанного).

---

## 3. Откат на предыдущую версию

Если после обновления что-то сломалось. Откат возвращает код **и** базу на момент перед
обновлением — данные, введённые после обновления, пропадут. Если их жалко — сначала напишите
в сессию, подскажу, как откатить без потерь.

```bash
cd /opt/uchet-v3
git log --oneline -5                      # найдите версию, записанную перед обновлением
ls -1t /opt/uchet-v3-backups/uchet3_before-update_*.dump | head -3   # последний бэкап перед обновлением
```

Подставьте код версии и имя файла бэкапа из вывода выше:

```bash
cd /opt/uchet-v3
git checkout КОД_ВЕРСИИ
docker compose up -d --build
./deploy/restore.sh /opt/uchet-v3-backups/ИМЯ_ФАЙЛА.dump
```

restore.sh попросит ввести `ДА`. **Должно вывести** в конце:
`Готово: база восстановлена, приложение отвечает`.

Проверка:

```bash
curl -s http://127.0.0.1:8001/api/health ; echo ; git -C /opt/uchet-v3 log -1 --oneline
```

**Должно вывести:** `{"status":"ok","db":"ok"}` и код той версии, на которую откатились.

> После отката папка «застывает» на старой версии. Следующее обновление (раздел 2) само
> вернёт её на `main` — команда `git checkout main` там уже есть.

---

## 4. Бэкап и восстановление

**Сделать бэкап вручную:**

```bash
/opt/uchet-v3/deploy/backup.sh
```
**Должно вывести:** `Бэкап готов: /opt/uchet-v3-backups/uchet3_...dump (размер)`.

**Список бэкапов:**

```bash
ls -lht /opt/uchet-v3-backups/ | head -20
```

**Проверить, что бэкап восстанавливается** (рабочую базу не трогает):

```bash
/opt/uchet-v3/deploy/backup_check.sh
```
**Должно вывести:** `ПРОВЕРКА ПРОЙДЕНА: бэкап восстанавливается`.

**Восстановить базу из бэкапа** (текущие данные заменятся; перед этим автоматически делается
страховочный бэкап `uchet3_before-restore_...`):

```bash
/opt/uchet-v3/deploy/restore.sh /opt/uchet-v3-backups/ИМЯ_ФАЙЛА.dump
```
Скрипт попросит ввести `ДА`. **Должно вывести:** `Готово: база восстановлена, приложение отвечает`.

**Журнал автоматических бэкапов:**

```bash
tail -20 /var/log/uchet3-backup.log
```
**Должно быть** по строке «Бэкап готов» за каждый день.

> Бэкапы лежат на том же сервере. При потере сервера они пропадут вместе с ним.
> Копирование бэкапов во внешнее хранилище настроим отдельным шагом.

---

## 5. Если что-то пошло не так

Пришлите в сессию вывод этих команд (секреты в них не попадают):

```bash
cd /opt/uchet-v3
docker compose ps
docker compose logs --tail 60 app
docker compose logs --tail 30 db
git log -1 --oneline
```

Если в логах `uchet3-app` есть «Приложение не запущено: ошибка в настройках (.env)» —
в `.env` не заполнен или слишком короткий `JWT_SECRET`. Это защита: без надёжного секрета
приложение специально не запускается. Проверьте командой:

```bash
grep -E '^(POSTGRES_PASSWORD|JWT_SECRET)=' /opt/uchet-v3/.env | awk -F= '{print $1, length($2), "символов"}'
```

Должно быть 48 и 64 символа. **Не меняйте** `POSTGRES_PASSWORD` после первого запуска —
база запомнила его при создании, и приложение перестанет к ней подключаться.

### «password authentication failed for user "uchet3"» (ошибка 500 при входе)

Значит, пароль базы в `.env` поменялся (например, повторно выполнен шаг 1.3), а база помнит
старый. Данные при этом целы. Команда задаёт базе пароль из текущего `.env` (выполняется внутри
контейнера базы, пароль на экран не выводится):

```bash
cd /opt/uchet-v3
docker compose exec db bash -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v pw="$POSTGRES_PASSWORD" <<< "ALTER USER CURRENT_USER PASSWORD :'\''pw'\'';"'
docker compose restart app
sleep 15
curl -s http://127.0.0.1:8001/api/health
```

**Должно вывести:** сначала `ALTER ROLE`, в конце — `{"status":"ok"...}`. Если при этом поменялся
и `JWT_SECRET`, всем пользователям нужно войти заново — это нормально.
