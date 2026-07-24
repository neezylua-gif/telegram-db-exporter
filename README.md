# Telegram Parser

Асинхронный архиватор Telegram на **Telethon 1.44** для Python **3.11+**. Сохраняет сообщения в SQLite, полный исходный объект в JSON, ссылки и состояние вложений.


## Установка

```bash
python -m venv .venv
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Linux/macOS:

```bash
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
nano .env
```

Получите `api_id` и `api_hash` на `my.telegram.org` и заполните `.env`. Telegram session даёт доступ к аккаунту: не публикуйте её, храните каталог `parser_data/sessions` на защищённом диске.

## Переменные окружения

```dotenv
TG_API_ID=
TG_API_HASH=
TG_PHONE=
TG_OUTPUT_DIR=parser_data
TG_SESSION=telegram_parser
TG_MEDIA_WORKERS=3
TG_DB_BATCH_SIZE=500
TG_MAX_MEDIA_SIZE_MB=100
TG_FLOOD_SLEEP_THRESHOLD=120
```

`TG_MAX_MEDIA_SIZE_MB=0` отключает ограничение размера. Используйте это значение только при контроле свободного места на диске.

## Команды

Показать диалоги:

```bash
python run.py dialogs
```

Разобрать до 10 000 сообщений без вложений:

```bash
python run.py parse --chat @username
```

Вся история и выбранные вложения:

```bash
python run.py parse --chat @username --limit 0 --media photo,document
```

Все типы вложений:

```bash
python run.py parse --chat @username --media all
```

Все диалоги:

```bash
python run.py parse --all --chat-workers 2
```

Опасная комбинация `--all --limit 0 --media all` требует явного подтверждения:

```bash
python run.py parse --all --limit 0 --media all --yes
```

Диапазон дат:

```bash
python run.py parse \
  --chat @username \
  --from-date 2025-01-01 \
  --to-date 2026-07-23
```

Перечитать историю с начала:

```bash
python run.py parse --chat @username --no-resume
```

Экспорт не требует Telegram credentials, если база уже существует:

```bash
python run.py export --format jsonl --output exports/messages.jsonl
python run.py export --format csv --output exports/messages.csv
```

CSV защищает значения, начинающиеся с `=`, `+`, `-`, `@`, табуляции и возврата каретки. Для намеренно сырого CSV:

```bash
python run.py export --format csv --output exports/raw.csv --raw-csv
```

После установки editable-пакета:

```bash
pip install -e .
tgparse dialogs
tgparse parse --chat @username
tgparse export --format jsonl --output messages.jsonl
```

## Восстановление media

Статусы media хранятся в SQLite. После аварийного завершения `downloading` сбрасывается в `pending`. При следующем запуске разбора того же чата записи `pending`, доступные `error` и отсутствующие локальные файлы повторно ставятся в очередь.

Для восстановления загрузок запустите тот же чат с нужным набором `--media`:

```bash
python run.py parse --chat @username --limit 1 --media all
```

`--limit 1` нужен только для минимального прохода истории; сохранённая очередь восстанавливается отдельно.

## Структура результата

```text
parser_data/
├── archive.sqlite3
├── sessions/
│   └── telegram_parser.session
└── media/
    └── -100123_name/
        └── 2026-07/
            └── 54321_document.pdf
```

Основные таблицы:

- `chats` — сведения о диалогах и checkpoint;
- `messages` — индексируемые поля и полный `raw_json`;
- `links` — уникальные URL;
- `media` — метаданные, статус, число попыток и ошибка загрузки;
- `parse_runs` — история запусков со статусами `running`, `completed`, `completed_with_errors`, `failed`, `cancelled`.

## Тесты

```bash
python -m unittest discover -s tests -v
```

Тесты не подключаются к Telegram и проверяют транзакции, миграции, безопасность экспорта, конфигурацию и утилиты.

## Важно

Парсер получает только данные, к которым авторизованный аккаунт уже имеет доступ. Соблюдайте правила Telegram, права участников и применимое законодательство. Не публикуйте приватные архивы без разрешения.
