from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime
from pathlib import Path

from .config import Settings, StorageSettings
from .db import ArchiveDatabase
from .exporter import export_messages
from .media import parse_media_selection
from .utils import parse_date

LOGGER = logging.getLogger(__name__)


def _bounded_int(
    name: str,
    *,
    minimum: int,
    maximum: int,
):
    def parse(value: str) -> int:
        try:
            number = int(value)
        except ValueError as exc:
            raise argparse.ArgumentTypeError(f"{name}: требуется целое число") from exc
        if not minimum <= number <= maximum:
            raise argparse.ArgumentTypeError(
                f"{name}: ожидается {minimum}..{maximum}, получено {number}"
            )
        return number

    return parse


def _media_argument(value: str) -> frozenset[str] | None:
    try:
        return parse_media_selection(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _date_argument(value: str):
    try:
        return parse_date(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def _end_date_argument(value: str):
    try:
        return parse_date(value, end_of_day=True)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tgparse",
        description="Производительный архиватор Telegram",
    )
    parser.add_argument("--env", default=".env", help="Путь к .env")
    parser.add_argument("--verbose", action="store_true", help="Подробный лог")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("dialogs", help="Показать доступные диалоги")

    parse_cmd = sub.add_parser("parse", help="Скачать сообщения и метаданные")
    target = parse_cmd.add_mutually_exclusive_group(required=True)
    target.add_argument("--chat", help="@username, t.me-ссылка или числовой ID")
    target.add_argument("--all", action="store_true", help="Все доступные диалоги")
    parse_cmd.add_argument(
        "--media",
        type=_media_argument,
        default=frozenset(),
        metavar="TYPES",
        help=(
            "none (по умолчанию), all или список: "
            "photo,video,audio,voice,document,sticker,gif,video_note"
        ),
    )
    parse_cmd.add_argument(
        "--limit",
        type=_bounded_int("--limit", minimum=0, maximum=100_000_000),
        default=10_000,
        help="Лимит сообщений на чат; 0 = вся история (по умолчанию 10000)",
    )
    parse_cmd.add_argument(
        "--no-resume",
        action="store_true",
        help="Перечитать историю с начала",
    )
    parse_cmd.add_argument("--from-date", type=_date_argument)
    parse_cmd.add_argument("--to-date", type=_end_date_argument)
    parse_cmd.add_argument(
        "--recheck-last",
        type=_bounded_int("--recheck-last", minimum=0, maximum=100_000),
        default=100,
        help="Повторно обновить N последних сообщений",
    )
    parse_cmd.add_argument(
        "--chat-workers",
        type=_bounded_int("--chat-workers", minimum=1, maximum=5),
        default=1,
        help="Параллельные чаты при --all",
    )
    parse_cmd.add_argument(
        "--yes",
        action="store_true",
        help="Подтвердить потенциально неограниченное скачивание",
    )

    export_cmd = sub.add_parser("export", help="Экспортировать SQLite в JSONL или CSV")
    export_cmd.add_argument("--format", choices=("jsonl", "csv"), required=True)
    export_cmd.add_argument("--output", type=Path, required=True)
    export_cmd.add_argument("--chat-id", type=int)
    export_cmd.add_argument(
        "--raw-csv",
        action="store_true",
        help="Не защищать CSV от формул Excel/LibreOffice",
    )
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command != "parse":
        return
    if args.from_date and args.to_date and args.from_date > args.to_date:
        parser.error("--from-date не может быть позже --to-date")
    unlimited_messages = args.limit == 0
    all_media = args.media is None
    if args.all and unlimited_messages and all_media and not args.yes:
        parser.error(
            "--all --limit 0 --media all может заполнить диск. "
            "Добавьте --yes, если это действительно задумано."
        )


async def _run_export(args: argparse.Namespace) -> int:
    storage = await asyncio.to_thread(StorageSettings.from_env, args.env)
    if not await asyncio.to_thread(storage.database_path.is_file):
        raise FileNotFoundError(f"SQLite-база не найдена: {storage.database_path}")
    database = ArchiveDatabase(storage.database_path)
    count = await export_messages(
        database,
        args.output,
        args.format,
        args.chat_id,
        raw_csv=args.raw_csv,
    )
    print(f"Экспортировано сообщений: {count}. Файл: {args.output}")
    return 0


async def _run_telegram_command(args: argparse.Namespace) -> int:
    from .parser import ParseOptions, TelegramArchiveParser

    settings = await asyncio.to_thread(Settings.from_env, args.env)
    async with ArchiveDatabase(settings.database_path) as database:
        options = ParseOptions()
        if args.command == "parse":
            options.media_types = args.media
            options.limit = args.limit or None
            options.resume = not args.no_resume
            options.from_date = args.from_date
            options.to_date = args.to_date
            options.recheck_last = args.recheck_last
            options.chat_workers = args.chat_workers

        async with TelegramArchiveParser(settings, database, options) as telegram:
            if args.command == "dialogs":
                dialogs = await telegram.list_dialogs()
                print(f"Найдено диалогов: {len(dialogs)}")
                for item in dialogs:
                    username = f"@{item['username']}" if item["username"] else "-"
                    print(
                        f"{item['chat_id']:>16}  {item['kind']:<10}  "
                        f"{username:<28}  {item['title']}"
                    )
                return 0

            target = "all" if args.all else str(args.chat)
            run_id = await database.create_run(
                datetime.now(UTC).isoformat(),
                target,
            )
            stats_dict: dict[str, int] = {"errors": 0}
            try:
                stats = await (
                    telegram.parse_all()
                    if args.all
                    else telegram.parse_one(args.chat)
                )
                await telegram.finish_media()
                stats_dict = stats.as_dict()
                stats_dict["media_downloaded"] = telegram.global_stats.media_downloaded
                stats_dict["media_skipped"] = telegram.global_stats.media_skipped
                stats_dict["errors"] = telegram.global_stats.errors
                status = (
                    "completed"
                    if stats_dict["errors"] == 0
                    else "completed_with_errors"
                )
                await database.finish_run(
                    run_id,
                    stats_dict,
                    status=status,
                )
            except asyncio.CancelledError:
                stats_dict["errors"] = max(1, stats_dict.get("errors", 0))
                await database.finish_run(
                    run_id,
                    stats_dict,
                    status="cancelled",
                    details="Выполнение отменено пользователем",
                )
                raise
            except Exception as exc:
                stats_dict["errors"] = max(1, stats_dict.get("errors", 0))
                await database.finish_run(
                    run_id,
                    stats_dict,
                    status="failed",
                    details=f"{type(exc).__name__}: {exc}"[:2000],
                )
                raise

            print("\nГотово")
            for key, value in stats_dict.items():
                print(f"  {key}: {value}")
            print(f"  database: {settings.database_path}")
            print(f"  media: {settings.media_dir}")
            return 0 if stats_dict["errors"] == 0 else 2


async def async_main(args: argparse.Namespace) -> int:
    if args.command == "export":
        return await _run_export(args)
    return await _run_telegram_command(args)


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    _validate_args(parser, args)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )
    try:
        code = asyncio.run(async_main(args))
    except KeyboardInterrupt:
        print("\nОстановлено пользователем. Зафиксированные транзакции сохранены.")
        code = 130
    except Exception as exc:
        LOGGER.exception("Критическая ошибка: %s", exc)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
