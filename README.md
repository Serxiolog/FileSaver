# AMPIS

Полная инструкция по запуску и устройству проекта: [ИНСТРУКЦИЯ.md](ИНСТРУКЦИЯ.md).

Микросервисное хранилище файлов до 1 МиБ. Flask/Jinja2 Gateway, четыре
FastAPI-сервиса со своими PostgreSQL и Worker, RabbitMQ и приватный RustFS.
В рабочей конфигурации ровно 15 контейнеров.

## Быстрый локальный запуск

Требуются Docker Engine, Docker Compose и Python 3.14. Текущая проверенная
конфигурация: Linux x86_64, один Docker-хост, по одному API-процессу сервиса.

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python infrastructure/start.py
```

Запуск создаёт локальные секреты, собирает образы, мигрирует четыре базы,
настраивает пользователей/топологию RabbitMQ, приватный bucket RustFS и
раздельные IAM-политики. Секреты и существующие JWT-ключи не перезаписываются.
Файл `.env` и каталог `secrets/` исключены из Git.

Открыть **http://localhost:8080/app/**. Пользователь может зарегистрироваться
через интерфейс. Регистрация не выполняет вход автоматически.
REST расположен под `/api/`. Консоль RustFS: `http://localhost:9001`, только
локальный административный доступ с credentials из `.env`.

Инфраструктурные образы закреплены digest в `infrastructure/images.json`,
Python-зависимости — в `requirements.lock` каждого приложения. UID/GID
контейнерных приложений соответствует пользователю хоста, поэтому закрытый
JWT-ключ доступен S1 без изменения его прав `0600`.

## Проверки

```bash
pytest -q
python tests/integration/stack_check.py
python tests/integration/fault_check.py
python tests/integration/web_check.py
python tests/integration/availability_check.py
python tests/integration/assignment_check.py
```

Интеграционные проверки временно останавливают отдельные контейнеры и создают
тестовых пользователей. Выполнять на локальном учебном экземпляре.
После тестов тестовые файлы удаляются, пользователи остаются. Fault-check
оставляет одно заведомо несовместимое событие в DLQ для проверки диагностики.
Результаты и точная область проверки: `docs/VERIFICATION.md`.

```bash
docker compose ps
docker compose logs --tail=50 upload-worker
docker compose down
# Последующая остановка/запуск без удаления постоянных томов:
docker compose up -d --wait
```

`docker compose down` сохраняет постоянные тома. Не добавлять `-v`, если
не требуется явное удаление данных. Для нового окружения или изменения IAM
использовать `infrastructure/start.py`, который повторяет bootstrap.

## Восстановление и архитектура

API и workers запускаются отдельно. Outbox/Inbox, подтверждения брокера,
отложенные retry и DLQ обеспечивают обработку повторных/поздних событий.
Worker healthcheck проверяет свежесть служебной отметки, а не только наличие
процесса. RabbitMQ имеет постоянное имя узла для сохранения данных при
пересоздании контейнера.

Удаление: ACTIVE → DELETING → DRAINING → ready → DeleteObject → DELETED.
Новые скачивания блокируются, существующие завершаются. Перезапущенный
Download API восстанавливает незавершённые сессии после получения блокировки
процесса. Подробности и границы single-host режима: `docs/OPERATIONS.md`.

Исходные требования сохранены в `docs/`. Канонические форматы:
`REST_API_CONTRACTS_v1.0.md` и `EVENT_CONTRACTS_v1.0.md`.

Сверка с исходным PDF: `docs/ASSIGNMENT_REVIEW.md`.
