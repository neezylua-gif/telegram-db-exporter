from __future__ import annotations

import asyncio
import csv
import json
import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .db import ArchiveDatabase

_DANGEROUS_CSV_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def _excel_safe(value: Any) -> Any:
    if isinstance(value, str) and value.startswith(_DANGEROUS_CSV_PREFIXES):
        return "'" + value
    return value


def _validate_output_path(database_path: Path, output: Path) -> tuple[Path, Path]:
    database_resolved = database_path.expanduser().resolve()
    output_resolved = output.expanduser().resolve()

    protected = {
        database_resolved,
        Path(str(database_resolved) + "-wal"),
        Path(str(database_resolved) + "-shm"),
    }
    if output_resolved in protected:
        raise ValueError(
            "Файл экспорта не может совпадать с SQLite-базой или её WAL/SHM"
        )
    return database_resolved, output_resolved


def _export_sync(
    database_path: Path,
    output: Path,
    export_format: str,
    chat_id: int | None,
    raw_csv: bool,
) -> int:
    if export_format not in {"jsonl", "csv"}:
        raise ValueError(f"Неизвестный формат экспорта: {export_format}")

    database_path, output = _validate_output_path(database_path, output)
    output.parent.mkdir(parents=True, exist_ok=True)

    where = "WHERE m.chat_id = ?" if chat_id is not None else ""
    params = (chat_id,) if chat_id is not None else ()

    query = f"""
        SELECT
            m.chat_id, c.title AS chat_title, c.username AS chat_username,
            m.message_id, m.date, m.edit_date, m.sender_id, m.sender_name,
            m.sender_username, m.text, m.message_kind, m.media_type,
            m.reply_to_message_id, m.grouped_id, m.views, m.forwards,
            m.replies_count, m.reactions_json, m.raw_json
        FROM messages m
        JOIN chats c ON c.chat_id = m.chat_id
        {where}
        ORDER BY m.chat_id, m.message_id
    """

    temp_path: Path | None = None
    connection: sqlite3.Connection | None = None
    try:
        uri = database_path.as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        cursor = connection.execute(query, params)

        handle = tempfile.NamedTemporaryFile(  # noqa: SIM115
            mode="w",
            encoding="utf-8" if export_format == "jsonl" else "utf-8-sig",
            newline="" if export_format == "csv" else None,
            prefix=f".{output.name}.",
            suffix=".tmp",
            dir=output.parent,
            delete=False,
        )
        temp_path = Path(handle.name)
        count = 0

        with handle:
            if export_format == "jsonl":
                for row in cursor:
                    item = dict(row)
                    for key in ("reactions_json", "raw_json"):
                        if item.get(key):
                            try:  # noqa: SIM105
                                item[key] = json.loads(item[key])
                            except json.JSONDecodeError:
                                pass

                    handle.write(json.dumps(item, ensure_ascii=False) + "\n")
                    count += 1
            else:
                writer: csv.DictWriter | None = None
                for row in cursor:
                    item = dict(row)
                    item.pop("raw_json", None)

                    if not raw_csv:
                        item = {
                            key: _excel_safe(value)
                            for key, value in item.items()
                        }

                    if writer is None:
                        writer = csv.DictWriter(handle, fieldnames=list(item))
                        writer.writeheader()

                    writer.writerow(item)
                    count += 1

            handle.flush()
            os.fsync(handle.fileno())

        os.replace(temp_path, output)
        temp_path = None
        return count
    finally:
        if connection is not None:
            connection.close()
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


async def export_messages(
    database: ArchiveDatabase,
    output: Path,
    export_format: str,
    chat_id: int | None = None,
    *,
    raw_csv: bool = False,
) -> int:
    """Экспортирует snapshot базы атомарной заменой конечного файла."""
    operation = asyncio.create_task(
        asyncio.to_thread(
            _export_sync,
            database.path,
            output,
            export_format,
            chat_id,
            raw_csv,
        )
    )
    try:
        return await asyncio.shield(operation)
    except asyncio.CancelledError:
        # Поток нельзя остановить безопасно посередине fsync/os.replace.
        await operation
        raise