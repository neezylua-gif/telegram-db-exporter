from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from helpers import chat_record, message_record

from tg_parser.db import SCHEMA_VERSION, ArchiveDatabase


class ArchiveDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "archive.sqlite3"

    async def asyncTearDown(self) -> None:
        self.temp.cleanup()

    async def test_batch_is_atomic(self) -> None:
        async with ArchiveDatabase(self.path) as database:
            await database.upsert_chat(chat_record())
            invalid_media = {
                "chat_id": -1001,
                "message_id": 1,
                "media_type": "document",
                "mime_type": "application/pdf",
                "original_name": "file.pdf",
                "remote_id": "123",
                "file_size": 10,
                "status": None,
            }
            with self.assertRaises(sqlite3.IntegrityError):
                await database.save_batch(
                    [message_record()],
                    [],
                    [invalid_media],
                )
            self.assertEqual(
                await database.fetch_scalar("SELECT COUNT(*) FROM messages"),
                0,
            )
            self.assertEqual(
                await database.fetch_scalar(
                    "SELECT last_message_id FROM chats WHERE chat_id=-1001"
                ),
                0,
            )

    async def test_old_schema_is_migrated(self) -> None:
        connection = sqlite3.connect(self.path)
        connection.executescript(
            """
            CREATE TABLE media (
                chat_id INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                media_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                PRIMARY KEY (chat_id, message_id)
            );
            """
        )
        connection.close()

        async with ArchiveDatabase(self.path) as database:
            columns = await database.fetch_scalar(
                "SELECT COUNT(*) FROM pragma_table_info('media') WHERE name='attempts'"
            )
            version = await database.fetch_scalar("PRAGMA user_version")
        self.assertEqual(columns, 1)
        self.assertEqual(version, SCHEMA_VERSION)

    async def test_missing_downloaded_file_is_requeued(self) -> None:
        async with ArchiveDatabase(self.path) as database:
            await database.upsert_chat(chat_record())
            message = message_record()
            message["media_type"] = "document"
            await database.save_batch(
                [message],
                [],
                [
                    {
                        "chat_id": -1001,
                        "message_id": 1,
                        "media_type": "document",
                        "mime_type": "application/pdf",
                        "original_name": "file.pdf",
                        "remote_id": "123",
                        "file_size": 10,
                        "status": "pending",
                    }
                ],
            )
            await database.update_media_result(
                -1001,
                1,
                status="downloaded",
                local_path=str(Path(self.temp.name) / "missing.pdf"),
            )
            self.assertTrue(await database.should_download_media(-1001, 1))
            status = await database.fetch_scalar(
                "SELECT status FROM media WHERE chat_id=-1001 AND message_id=1"
            )
            self.assertEqual(status, "pending")

    async def test_media_claim_is_atomic_between_connections(self) -> None:
        first = ArchiveDatabase(self.path)
        second = ArchiveDatabase(self.path)
        await first.open()
        try:
            await first.upsert_chat(chat_record())
            message = message_record()
            message["media_type"] = "document"
            await first.save_batch(
                [message],
                [],
                [
                    {
                        "chat_id": -1001,
                        "message_id": 1,
                        "media_type": "document",
                        "mime_type": "application/pdf",
                        "original_name": "file.pdf",
                        "remote_id": "123",
                        "file_size": 10,
                        "status": "pending",
                    }
                ],
            )
            self.assertTrue(await first.claim_media_download(-1001, 1))
            await second.open()
            try:
                self.assertFalse(await second.claim_media_download(-1001, 1))
            finally:
                await second.close()
        finally:
            await first.close()

    async def test_expired_media_lease_is_recovered(self) -> None:
        first = ArchiveDatabase(self.path)
        await first.open()
        try:
            await first.upsert_chat(chat_record())
            message = message_record()
            message["media_type"] = "document"
            await first.save_batch(
                [message],
                [],
                [
                    {
                        "chat_id": -1001,
                        "message_id": 1,
                        "media_type": "document",
                        "mime_type": "application/pdf",
                        "original_name": "file.pdf",
                        "remote_id": "123",
                        "file_size": 10,
                        "status": "pending",
                    }
                ],
            )
            await first.update_media_result(
                -1001,
                1,
                status="downloading",
                next_retry_at="2000-01-01T00:00:00+00:00",
            )
        finally:
            await first.close()

        async with ArchiveDatabase(self.path) as recovered:
            status = await recovered.fetch_scalar(
                "SELECT status FROM media WHERE chat_id=-1001 AND message_id=1"
            )
            self.assertEqual(status, "pending")


if __name__ == "__main__":
    unittest.main()
