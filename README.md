# AST-Thinker

LLM-бот с краткосрочной и долгосрочной памятью, периодическими саморазмышлениями и веб-интерфейсом (Flask).

## Установка

**Рекомендуемая версия Python:** 3.11 или 3.12. На Windows с Python 3.13 пакеты `aiohttp` и `pydantic-core` (зависимости aiogram) часто не имеют готовых колёс и требуют сборки из исходников (нужны Microsoft C++ Build Tools и линкер MSVC).

```bash
pip install -r requirements.txt
```

**Если на Windows с Python 3.13 установка падает с ошибкой сборки aiohttp/pydantic-core:**

1. **Вариант А (проще):** установите Python 3.11 или 3.12 с [python.org](https://www.python.org/downloads/), создайте виртуальное окружение и в нём выполните `pip install -r requirements.txt`.
2. **Вариант Б:** установите [Build Tools for Visual Studio](https://visualstudio.microsoft.com/visual-cpp-build-tools/) с компонентом «Разработка классических приложений на C++» (для `link.exe` и компилятора). После установки перезапустите терминал и снова выполните `pip install -r requirements.txt`.

Скопируйте `.env.example` в `.env`. По умолчанию используется локальный API (например `http://127.0.0.1:1234` для LM Studio на той же машине; GET /v1/models, POST /v1/chat/completions). При необходимости задайте `API_BASE_URL`, `API_KEY`, `MODEL_NAME`.

## Запуск в Docker (Docker Compose)

Убедитесь, что создан файл `.env` (скопируйте из `.env.example`). При запуске из другой сети задайте в `.env` переменную `API_BASE_URL` (адрес LLM API, доступный из контейнера: например `http://host.docker.internal:1234` на Windows/macOS для доступа к хосту).

```bash
docker compose up -d
```

Веб-интерфейс: http://127.0.0.1:7111. Данные БД и загрузки сохраняются в томах `thinker_data` и `thinker_uploads`.

Остановка: `docker compose down`.

Проверка доступа к LM Studio из контейнера (после `docker compose up -d`):

```bash
docker compose exec thinker python -m app.check_llm
```

Скрипт проверяет GET /v1/models и короткий запрос в чат; при ошибке выведет причину (неверный API_BASE_URL, LM Studio не запущен и т.п.).

## Запуск без Docker

Из корня проекта:

```bash
python -m app.main
```

Откройте в браузере: http://127.0.0.1:7111

- **Чат** — общение с ботом, возможность приложить файл (txt, md, py и др.) как «пищу для размышлений».
- **Журнал мыслей** — текущие саморазмышления (краткосрочная память).
- **Долговременная память** — записи, помеченные как важные; можно добавлять вручную.
- **Настройки** — периодичность циклов саморазмышления (сек), лимит токенов на саморазмышление, первичный промпт.

## Структура

- `app/config.py` — конфигурация из .env
- `app/db.py` — SQLite, модели памяти, настроек, чата, вложений
- `app/llm.py` — вызов Upstage API, саморазмышление, ответ пользователю
- `app/scheduler.py` — фоновый цикл саморазмышлений
- `app/api.py` — Flask: страницы и API
- `app/bot.py` — Telegram-бот (aiogram 3.x), опционально
- `app/main.py` — инициализация БД, планировщик, запуск Flask
