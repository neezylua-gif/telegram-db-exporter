from __future__ import annotations

import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime
from pathlib import Path


class DummyTelegramClient:
    def __init__(self, *args, **kwargs) -> None:
        pass


class DummyRPCError(Exception):
    pass


class DummyFloodWaitError(DummyRPCError):
    seconds = 1


telethon = types.ModuleType("telethon")
telethon.TelegramClient = DummyTelegramClient
telethon.utils = types.SimpleNamespace(
    get_display_name=lambda sender: getattr(sender, "name", ""),
    get_peer_id=lambda entity: int(entity.id),
)
errors = types.ModuleType("telethon.errors")
errors.RPCError = DummyRPCError
errors.FloodWaitError = DummyFloodWaitError
sys.modules["telethon"] = telethon
sys.modules["telethon.errors"] = errors

from tg_parser.config import Settings
from tg_parser.db import ArchiveDatabase
from tg_parser.parser import MediaJob, ParseOptions, TelegramArchiveParser

from helpers import chat_record, message_record


class DownloadingMessage:
    async def download_media(self, *, file: str) -> str:
        Path(file).write_bytes(b"data")
        return file


class ParserUnitTests(unittest.IsolatedAsyncioTestCase):
    async def test_download_uses_part_file_and_marks_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            settings = Settings(
                api_id=1,
                api_hash="hash",
                phone=None,
                session=root / "sessions" / "test",
                output_dir=root,
                media_workers=1,
                db_batch_size=10,
                max_media_size_mb=10,
                flood_sleep_threshold=120,
            )
            settings.prepare_directories()
            async with ArchiveDatabase(settings.database_path) as database:
                await database.upsert_chat(chat_record())
                message = message_record()
                message["media_type"] = "document"
                await database.save_batch(
                    [message],
                    [],
                    [{
                        "chat_id": -1001,
                        "message_id": 1,
                        "media_type": "document",
                        "mime_type": "application/pdf",
                        "original_name": "file.pdf",
                        "remote_id": "123",
                        "file_size": 4,
                        "status": "pending",
                    }],
                )
                parser = TelegramArchiveParser(settings, database, ParseOptions())
                job = MediaJob(
                    chat_id=-1001,
                    chat_slug="-1001_Test",
                    message_id=1,
                    date=datetime(2026, 7, 23, tzinfo=UTC),
                    message=DownloadingMessage(),
                    media_type="document",
                    original_name="file.pdf",
                    mime_type="application/pdf",
                    file_size=4,
                )
                await parser._download_media(job, 1)
                status = await database.fetch_scalar(
                    "SELECT status FROM media WHERE chat_id=-1001 AND message_id=1"
                )
                local_path = await database.fetch_scalar(
                    "SELECT local_path FROM media WHERE chat_id=-1001 AND message_id=1"
                )
                self.assertEqual(status, "downloaded")
                self.assertTrue(Path(local_path).is_file())
                self.assertFalse(any(root.rglob("*.part")))


if __name__ == "__main__":
    unittest.main()
