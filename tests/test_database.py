import asyncio
from pathlib import Path

from tg_parser.db import ArchiveDatabase, MESSAGE_COLUMNS


def message(chat_id: int, message_id: int, text: str = "text"):
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


def chat(chat_id: int):
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


def media(chat_id: int, message_id: int, remote_id: str):
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


def test_database_upsert_and_stale_cleanup(tmp_path: Path):
    async def scenario():
        db = ArchiveDatabase(tmp_path / "archive.sqlite3")
        await db.open()
        try:
            await db.upsert_chat(chat(-1001))
            await db.save_batch(
                [message(-1001, 1)],
                [(-1001, 1, "https://example.com")],
                [media(-1001, 1, "remote-1")],
            )
            await db.update_media_result(
                -1001, 1, status="downloaded", local_path="/tmp/file.pdf"
            )

            # Та же сигнатура медиа сохраняет downloaded и путь.
            await db.save_batch(
                [message(-1001, 1, "edited")],
                [],
                [media(-1001, 1, "remote-1")],
            )
            row = db.conn.execute(
                "SELECT status, local_path FROM media WHERE chat_id=-1001 AND message_id=1"
            ).fetchone()
            assert tuple(row) == ("downloaded", "/tmp/file.pdf")
            assert db.conn.execute("SELECT COUNT(*) FROM links").fetchone()[0] == 0

            # Новое вложение сбрасывает путь и возвращает pending.
            await db.save_batch(
                [message(-1001, 1, "new file")],
                [],
                [media(-1001, 1, "remote-2")],
            )
            row = db.conn.execute(
                "SELECT status, local_path FROM media WHERE chat_id=-1001 AND message_id=1"
            ).fetchone()
            assert tuple(row) == ("pending", None)

            # Удалённое вложение удаляет устаревшую строку media.
            await db.save_batch([message(-1001, 1, "no file")], [], [])
            assert db.conn.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 0
            assert await db.get_last_message_id(-1001) == 1
        finally:
            await db.close()

    asyncio.run(scenario())
