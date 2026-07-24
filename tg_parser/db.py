from __future__ import annotations

import asyncio
import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2
MEDIA_LEASE_HOURS = 6

SCHEMA = """
CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    raw_id INTEGER,
    peer_type TEXT NOT NULL,
    title TEXT,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    participants_count INTEGER,
    is_broadcast INTEGER NOT NULL DEFAULT 0 CHECK (is_broadcast IN (0, 1)),
    is_megagroup INTEGER NOT NULL DEFAULT 0 CHECK (is_megagroup IN (0, 1)),
    raw_json TEXT,
    last_message_id INTEGER NOT NULL DEFAULT 0,
    last_parsed_at TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date TEXT,
    edit_date TEXT,
    sender_id INTEGER,
    sender_name TEXT,
    sender_username TEXT,
    text TEXT,
    message_kind TEXT NOT NULL,
    media_type TEXT,
    reply_to_message_id INTEGER,
    grouped_id INTEGER,
    via_bot_id INTEGER,
    views INTEGER,
    forwards INTEGER,
    replies_count INTEGER,
    is_outgoing INTEGER NOT NULL DEFAULT 0 CHECK (is_outgoing IN (0, 1)),
    is_pinned INTEGER NOT NULL DEFAULT 0 CHECK (is_pinned IN (0, 1)),
    is_silent INTEGER NOT NULL DEFAULT 0 CHECK (is_silent IN (0, 1)),
    is_post INTEGER NOT NULL DEFAULT 0 CHECK (is_post IN (0, 1)),
    post_author TEXT,
    forward_json TEXT,
    reactions_json TEXT,
    entities_json TEXT,
    action_json TEXT,
    media_json TEXT,
    raw_json TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id),
    FOREIGN KEY (chat_id) REFERENCES chats(chat_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS links (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    url TEXT NOT NULL,
    PRIMARY KEY (chat_id, message_id, url),
    FOREIGN KEY (chat_id, message_id)
        REFERENCES messages(chat_id, message_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS media (
    chat_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    media_type TEXT NOT NULL,
    mime_type TEXT,
    original_name TEXT,
    remote_id TEXT,
    file_size INTEGER,
    local_path TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    error TEXT,
    updated_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    next_retry_at TEXT,
    PRIMARY KEY (chat_id, message_id),
    FOREIGN KEY (chat_id, message_id)
        REFERENCES messages(chat_id, message_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS parse_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    target TEXT NOT NULL,
    status TEXT NOT NULL,
    messages_seen INTEGER NOT NULL DEFAULT 0,
    messages_saved INTEGER NOT NULL DEFAULT 0,
    media_queued INTEGER NOT NULL DEFAULT 0,
    media_downloaded INTEGER NOT NULL DEFAULT 0,
    errors INTEGER NOT NULL DEFAULT 0,
    details TEXT
);

CREATE INDEX IF NOT EXISTS idx_messages_chat_date ON messages(chat_id, date);
CREATE INDEX IF NOT EXISTS idx_messages_sender ON messages(sender_id);
CREATE INDEX IF NOT EXISTS idx_messages_media_type ON messages(media_type);
CREATE INDEX IF NOT EXISTS idx_links_url ON links(url);
"""

MESSAGE_COLUMNS = (
    "chat_id",
    "message_id",
    "date",
    "edit_date",
    "sender_id",
    "sender_name",
    "sender_username",
    "text",
    "message_kind",
    "media_type",
    "reply_to_message_id",
    "grouped_id",
    "via_bot_id",
    "views",
    "forwards",
    "replies_count",
    "is_outgoing",
    "is_pinned",
    "is_silent",
    "is_post",
    "post_author",
    "forward_json",
    "reactions_json",
    "entities_json",
    "action_json",
    "media_json",
    "raw_json",
)

MESSAGE_UPSERT = f"""
INSERT INTO messages ({','.join(MESSAGE_COLUMNS)})
VALUES ({','.join('?' for _ in MESSAGE_COLUMNS)})
ON CONFLICT(chat_id, message_id) DO UPDATE SET
    date=excluded.date,
    edit_date=excluded.edit_date,
    sender_id=excluded.sender_id,
    sender_name=excluded.sender_name,
    sender_username=excluded.sender_username,
    text=excluded.text,
    message_kind=excluded.message_kind,
    media_type=excluded.media_type,
    reply_to_message_id=excluded.reply_to_message_id,
    grouped_id=excluded.grouped_id,
    via_bot_id=excluded.via_bot_id,
    views=excluded.views,
    forwards=excluded.forwards,
    replies_count=excluded.replies_count,
    is_outgoing=excluded.is_outgoing,
    is_pinned=excluded.is_pinned,
    is_silent=excluded.is_silent,
    is_post=excluded.is_post,
    post_author=excluded.post_author,
    forward_json=excluded.forward_json,
    reactions_json=excluded.reactions_json,
    entities_json=excluded.entities_json,
    action_json=excluded.action_json,
    media_json=excluded.media_json,
    raw_json=excluded.raw_json
"""


class ArchiveDatabase:
    """SQLite-архив с сериализованным доступом из рабочего потока."""

    def __init__(self, path: Path):
        self.path = path
        self.connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    async def __aenter__(self) -> "ArchiveDatabase":
        await self.open()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close(commit=exc_type is None)

    @property
    def conn(self) -> sqlite3.Connection:
        if self.connection is None:
            raise RuntimeError("Database is not open")
        return self.connection

    async def _locked_call(self, function, /, *args):
        async with self._lock:
            operation = asyncio.create_task(asyncio.to_thread(function, *args))
            try:
                return await asyncio.shield(operation)
            except asyncio.CancelledError:
                await operation
                raise

    async def open(self) -> None:
        await self._locked_call(self._open_sync)

    def _open_sync(self) -> None:
        if self.connection is not None:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            self.path,
            timeout=30.0,
            check_same_thread=False,
        )
        try:
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=NORMAL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.executescript(SCHEMA)
            if os.name == "posix":
                self.path.chmod(0o600)
            self.connection = connection
            self._migrate_sync()
            with connection:
                connection.execute(
                    """
                    UPDATE media
                    SET status='pending', next_retry_at=NULL
                    WHERE status='downloading'
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    """,
                    (datetime.now(UTC).isoformat(),),
                )
        except BaseException:
            connection.close()
            self.connection = None
            raise

    def _migrate_sync(self) -> None:
        connection = self.conn
        connection.execute("BEGIN IMMEDIATE")
        try:
            media_columns = {
                row["name"] for row in connection.execute("PRAGMA table_info(media)")
            }
            if "attempts" not in media_columns:
                connection.execute(
                    "ALTER TABLE media ADD COLUMN attempts INTEGER NOT NULL DEFAULT 0"
                )
            if "next_retry_at" not in media_columns:
                connection.execute(
                    "ALTER TABLE media ADD COLUMN next_retry_at TEXT"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_media_status_retry "
                "ON media(status, next_retry_at)"
            )
            connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        except BaseException:
            connection.rollback()
            raise

    async def close(self, *, commit: bool = True) -> None:
        await self._locked_call(self._close_sync, commit)

    def _close_sync(self, commit: bool) -> None:
        if self.connection is None:
            return
        try:
            if self.connection.in_transaction:
                if commit:
                    self.connection.commit()
                else:
                    self.connection.rollback()
        finally:
            self.connection.close()
            self.connection = None

    async def upsert_chat(self, chat: dict[str, Any]) -> None:
        await self._locked_call(self._upsert_chat_sync, chat)

    def _upsert_chat_sync(self, chat: dict[str, Any]) -> None:
        keys = (
            "chat_id", "raw_id", "peer_type", "title", "username",
            "first_name", "last_name", "participants_count",
            "is_broadcast", "is_megagroup", "raw_json", "last_parsed_at",
        )
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO chats (
                    chat_id, raw_id, peer_type, title, username, first_name,
                    last_name, participants_count, is_broadcast, is_megagroup,
                    raw_json, last_parsed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    raw_id=excluded.raw_id,
                    peer_type=excluded.peer_type,
                    title=excluded.title,
                    username=excluded.username,
                    first_name=excluded.first_name,
                    last_name=excluded.last_name,
                    participants_count=excluded.participants_count,
                    is_broadcast=excluded.is_broadcast,
                    is_megagroup=excluded.is_megagroup,
                    raw_json=excluded.raw_json,
                    last_parsed_at=excluded.last_parsed_at
                """,
                tuple(chat[key] for key in keys),
            )

    async def get_last_message_id(self, chat_id: int) -> int:
        return await self._locked_call(self._get_last_message_id_sync, chat_id)

    def _get_last_message_id_sync(self, chat_id: int) -> int:
        row = self.conn.execute(
            "SELECT last_message_id FROM chats WHERE chat_id = ?", (chat_id,)
        ).fetchone()
        return int(row[0]) if row else 0

    async def save_batch(
        self,
        messages: list[dict[str, Any]],
        links: list[tuple[int, int, str]],
        media_rows: list[dict[str, Any]],
    ) -> int:
        if not messages:
            return 0
        return await self._locked_call(
            self._save_batch_sync, messages, links, media_rows
        )

    def _save_batch_sync(
        self,
        messages: list[dict[str, Any]],
        links: list[tuple[int, int, str]],
        media_rows: list[dict[str, Any]],
    ) -> int:
        chat_ids = {int(item["chat_id"]) for item in messages}
        if len(chat_ids) != 1:
            raise ValueError("Один batch не может содержать сообщения разных чатов")
        chat_id = next(iter(chat_ids))
        max_message_id = max(int(item["message_id"]) for item in messages)
        values = [tuple(item[column] for column in MESSAGE_COLUMNS) for item in messages]
        message_keys = [
            (int(item["chat_id"]), int(item["message_id"])) for item in messages
        ]

        with self.conn:
            self.conn.executemany(MESSAGE_UPSERT, values)
            self.conn.executemany(
                "DELETE FROM links WHERE chat_id=? AND message_id=?", message_keys
            )
            if links:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO links(chat_id, message_id, url) VALUES (?, ?, ?)",
                    links,
                )

            media_keys = {
                (int(row["chat_id"]), int(row["message_id"])) for row in media_rows
            }
            stale_media_keys = [key for key in message_keys if key not in media_keys]
            if stale_media_keys:
                self.conn.executemany(
                    "DELETE FROM media WHERE chat_id=? AND message_id=?",
                    stale_media_keys,
                )

            if media_rows:
                self.conn.executemany(
                    """
                    INSERT INTO media (
                        chat_id, message_id, media_type, mime_type,
                        original_name, remote_id, file_size, status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(chat_id, message_id) DO UPDATE SET
                        media_type=excluded.media_type,
                        mime_type=excluded.mime_type,
                        original_name=excluded.original_name,
                        remote_id=excluded.remote_id,
                        file_size=excluded.file_size,
                        local_path=CASE
                            WHEN media.status='downloaded'
                             AND media.remote_id IS excluded.remote_id
                             AND media.file_size IS excluded.file_size
                            THEN media.local_path ELSE NULL END,
                        error=CASE
                            WHEN media.status='downloaded'
                             AND media.remote_id IS excluded.remote_id
                             AND media.file_size IS excluded.file_size
                            THEN media.error ELSE NULL END,
                        status=CASE
                            WHEN media.status='downloaded'
                             AND media.remote_id IS excluded.remote_id
                             AND media.file_size IS excluded.file_size
                            THEN media.status ELSE excluded.status END,
                        next_retry_at=NULL
                    """,
                    [
                        (
                            row["chat_id"], row["message_id"], row["media_type"],
                            row.get("mime_type"), row.get("original_name"),
                            row.get("remote_id"), row.get("file_size"), row["status"],
                        )
                        for row in media_rows
                    ],
                )

            self.conn.execute(
                """
                UPDATE chats
                SET last_message_id = MAX(last_message_id, ?), last_parsed_at = ?
                WHERE chat_id = ?
                """,
                (max_message_id, datetime.now(UTC).isoformat(), chat_id),
            )
        return len(messages)

    async def should_download_media(self, chat_id: int, message_id: int) -> bool:
        return await self._locked_call(
            self._should_download_media_sync, chat_id, message_id
        )

    def _should_download_media_sync(self, chat_id: int, message_id: int) -> bool:
        row = self.conn.execute(
            """
            SELECT status, local_path, file_size
            FROM media WHERE chat_id=? AND message_id=?
            """,
            (chat_id, message_id),
        ).fetchone()
        if row is None:
            return False
        if row["status"] != "downloaded" or not row["local_path"]:
            return row["status"] not in {"disabled", "skipped_size"}

        path = Path(row["local_path"])
        try:
            stat_result = path.stat()
            valid = path.is_file() and stat_result.st_size > 0
            if valid and row["file_size"] is not None:
                valid = stat_result.st_size == int(row["file_size"])
        except OSError:
            valid = False
        if valid:
            return False

        with self.conn:
            self.conn.execute(
                """
                UPDATE media
                SET status='pending', local_path=NULL,
                    error='Локальный файл отсутствует или повреждён',
                    updated_at=?, next_retry_at=NULL
                WHERE chat_id=? AND message_id=?
                """,
                (datetime.now(UTC).isoformat(), chat_id, message_id),
            )
        return True

    async def list_media_for_recovery(
        self,
        chat_id: int,
        media_types: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        return await self._locked_call(
            self._list_media_for_recovery_sync, chat_id, media_types
        )

    def _list_media_for_recovery_sync(
        self,
        chat_id: int,
        media_types: frozenset[str] | None,
    ) -> list[dict[str, Any]]:
        parameters: list[Any] = [chat_id]
        media_filter = ""
        if media_types is not None:
            if not media_types:
                return []
            placeholders = ",".join("?" for _ in media_types)
            media_filter = f" AND media_type IN ({placeholders})"
            parameters.extend(sorted(media_types))
        rows = self.conn.execute(
            f"""
            SELECT chat_id, message_id, media_type, mime_type, original_name,
                   remote_id, file_size, local_path, status, attempts
            FROM media
            WHERE chat_id=?
              AND status NOT IN ('disabled', 'skipped_size', 'missing')
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
              {media_filter}
            ORDER BY message_id
            """,
            [parameters[0], datetime.now(UTC).isoformat(), *parameters[1:]],
        ).fetchall()

        recoverable: list[dict[str, Any]] = []
        invalid_downloaded: list[tuple[int, int]] = []
        for row in rows:
            item = dict(row)
            if item["status"] == "downloaded" and item.get("local_path"):
                path = Path(item["local_path"])
                try:
                    stat_result = path.stat()
                    valid = path.is_file() and stat_result.st_size > 0
                    if valid and item.get("file_size") is not None:
                        valid = stat_result.st_size == int(item["file_size"])
                except OSError:
                    valid = False
                if valid:
                    continue
                invalid_downloaded.append((int(item["chat_id"]), int(item["message_id"])))
                item["status"] = "pending"
                item["local_path"] = None
            recoverable.append(item)

        if invalid_downloaded:
            with self.conn:
                self.conn.executemany(
                    """
                    UPDATE media
                    SET status='pending', local_path=NULL,
                        error='Локальный файл отсутствует или повреждён',
                        updated_at=?, next_retry_at=NULL
                    WHERE chat_id=? AND message_id=?
                    """,
                    [
                        (datetime.now(UTC).isoformat(), chat_id, message_id)
                        for chat_id, message_id in invalid_downloaded
                    ],
                )
        return recoverable

    async def claim_media_download(self, chat_id: int, message_id: int) -> bool:
        return await self._locked_call(
            self._claim_media_download_sync, chat_id, message_id
        )

    def _claim_media_download_sync(self, chat_id: int, message_id: int) -> bool:
        now = datetime.now(UTC)
        lease_until = now + timedelta(hours=MEDIA_LEASE_HOURS)
        with self.conn:
            cursor = self.conn.execute(
                """
                UPDATE media
                SET status='downloading', attempts=attempts+1,
                    error=NULL, updated_at=?, next_retry_at=?
                WHERE chat_id=? AND message_id=?
                  AND (
                        status IN ('pending', 'error')
                        OR (status='downloading' AND next_retry_at <= ?)
                      )
                  AND (next_retry_at IS NULL OR next_retry_at <= ?)
                """,
                (
                    now.isoformat(),
                    lease_until.isoformat(),
                    chat_id,
                    message_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
        return cursor.rowcount == 1

    async def update_media_result(
        self,
        chat_id: int,
        message_id: int,
        *,
        status: str,
        local_path: str | None = None,
        error: str | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        await self._locked_call(
            self._update_media_result_sync,
            chat_id,
            message_id,
            status,
            local_path,
            error,
            next_retry_at,
        )

    def _update_media_result_sync(
        self,
        chat_id: int,
        message_id: int,
        status: str,
        local_path: str | None,
        error: str | None,
        next_retry_at: str | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE media
                SET status=?, local_path=?, error=?, updated_at=?, next_retry_at=?
                WHERE chat_id=? AND message_id=?
                """,
                (
                    status,
                    local_path,
                    error,
                    datetime.now(UTC).isoformat(),
                    next_retry_at,
                    chat_id,
                    message_id,
                ),
            )

    async def create_run(self, started_at: str, target: str) -> int:
        return await self._locked_call(self._create_run_sync, started_at, target)

    def _create_run_sync(self, started_at: str, target: str) -> int:
        with self.conn:
            cursor = self.conn.execute(
                "INSERT INTO parse_runs(started_at, target, status) VALUES (?, ?, 'running')",
                (started_at, target),
            )
        if cursor.lastrowid is None:
            raise RuntimeError("SQLite не вернул ID запуска")
        return int(cursor.lastrowid)

    async def finish_run(
        self,
        run_id: int,
        stats: dict[str, Any],
        *,
        status: str,
        details: str | None = None,
    ) -> None:
        await self._locked_call(
            self._finish_run_sync, run_id, stats, status, details
        )

    def _finish_run_sync(
        self,
        run_id: int,
        stats: dict[str, Any],
        status: str,
        details: str | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE parse_runs SET
                    finished_at=?, status=?, messages_seen=?, messages_saved=?,
                    media_queued=?, media_downloaded=?, errors=?, details=?
                WHERE run_id=?
                """,
                (
                    datetime.now(UTC).isoformat(),
                    status,
                    stats.get("messages_seen", 0),
                    stats.get("messages_saved", 0),
                    stats.get("media_queued", 0),
                    stats.get("media_downloaded", 0),
                    stats.get("errors", 0),
                    details,
                    run_id,
                ),
            )

    async def fetch_scalar(
        self,
        query: str,
        parameters: Iterable[Any] = (),
    ) -> Any:
        return await self._locked_call(
            self._fetch_scalar_sync, query, tuple(parameters)
        )

    def _fetch_scalar_sync(self, query: str, parameters: tuple[Any, ...]) -> Any:
        row = self.conn.execute(query, parameters).fetchone()
        return None if row is None else row[0]
