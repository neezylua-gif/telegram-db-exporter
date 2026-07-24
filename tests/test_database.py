from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from tg_parser.db import MESSAGE_COLUMNS, ArchiveDatabase


def message(
    chat_id: int,
    message_id: int,
    text: str = "text",
) -> dict[str, Any]:
    row = {column: None for column in MESSAGE_COLUMNS}
    row.update(
        chat_id=chat_id,
        message_id=message_id,
        text=text,
        message_kind="message",
        is_outgoing=0,
        is_pinned=0,
        is_silent=0,
        is_post=0,
        raw_json="{}",
    )
    return row


def chat(chat_id: int) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "raw_id": abs(chat_id),
        "peer_type": "channel",
        "title": "Test",
        "username": "test",
        "first_name": None,
        "last_name": None,
        "participants_count": None,
        "is_broadcast": 1,
        "is_megagroup": 0,
        "raw_json": "{}",
        "last_parsed_at": "2026-01-01T00:00:00+00:00",
    }


def media(
    chat_id: int,
    message_id: int,
    remote_id: str,
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "media_type": "document",
        "mime_type": "application/pdf",
        "original_name": "file.pdf",
        "remote_id": remote_id,
        "file_size": 100,
        "status": "pending",
    }


class DatabaseBehaviorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database_path = self.root / "archive.sqlite3"

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_database_upsert_and_stale_cleanup(self) -> None:
        downloaded_path = self.root / "file.pdf"
        downloaded_path.write_bytes(b"x" * 100)

        async with ArchiveDatabase(self.database_path) as database:
            await database.upsert_chat(chat(-1001))
            await database.save_batch(
                [message(-1001, 1)],
                [(-1001, 1, "https://example.com")],
                [media(-1001, 1, "remote-1")],
            )
            await database.update_media_result(
                -1001,
                1,
                status="downloaded",
                local_path=str(downloaded_path),
            )

            # Та же сигнатура медиа сохраняет downloaded и локальный путь.
            await database.save_batch(
                [message(-1001, 1, "edited")],
                [],
                [media(-1001, 1, "remote-1")],
            )
            row = database.conn.execute(
                """
                SELECT status, local_path
                FROM media
                WHERE chat_id=-1001 AND message_id=1
                """
            ).fetchone()
            self.assertEqual(tuple(row), ("downloaded", str(downloaded_path)))
            self.assertEqual(
                database.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0],
                0,
            )

            # Новое вложение сбрасывает путь и возвращает pending.
            await database.save_batch(
                [message(-1001, 1, "new file")],
                [],
                [media(-1001, 1, "remote-2")],
            )
            row = database.conn.execute(
                """
                SELECT status, local_path
                FROM media
                WHERE chat_id=-1001 AND message_id=1
                """
            ).fetchone()
            self.assertEqual(tuple(row), ("pending", None))

            # Удалённое вложение удаляет устаревшую строку media.
            await database.save_batch([message(-1001, 1, "no file")], [], [])
            self.assertEqual(
                database.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0],
                0,
            )
            self.assertEqual(await database.get_last_message_id(-1001), 1)


if __name__ == "__main__":
    unittest.main()
