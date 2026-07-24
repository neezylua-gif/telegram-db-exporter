from __future__ import annotations

import asyncio
import logging
import mimetypes
import os
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils as telethon_utils
from telethon.errors import FloodWaitError, RPCError

from .config import Settings
from .db import ArchiveDatabase
from .media import DOWNLOADABLE_MEDIA
from .utils import (
    extract_urls,
    json_dumps,
    normalize_chat_reference,
    safe_extension,
    sanitize_component,
    tl_dict,
    utc_iso,
)

LOGGER = logging.getLogger(__name__)

MEDIA_FETCH_BATCH = 100
MAX_ERROR_LENGTH = 1000


@dataclass(slots=True)
class ParseOptions:
    media_types: frozenset[str] | None = frozenset()
    limit: int | None = 10_000
    resume: bool = True
    from_date: datetime | None = None
    to_date: datetime | None = None
    recheck_last: int = 100
    chat_workers: int = 1

    def media_allowed(self, media_type: str | None) -> bool:
        return (
            media_type is not None
            and media_type in DOWNLOADABLE_MEDIA
            and (self.media_types is None or media_type in self.media_types)
        )


@dataclass(slots=True)
class ParseStats:
    chats: int = 0
    messages_seen: int = 0
    messages_saved: int = 0
    media_queued: int = 0
    media_downloaded: int = 0
    media_skipped: int = 0
    links: int = 0
    errors: int = 0

    def merge(self, other: "ParseStats") -> None:
        for item in fields(self):
            setattr(self, item.name, getattr(self, item.name) + getattr(other, item.name))

    def as_dict(self) -> dict[str, int]:
        return {item.name: getattr(self, item.name) for item in fields(self)}


@dataclass(slots=True)
class MediaJob:
    chat_id: int
    chat_slug: str
    message_id: int
    date: datetime | None
    message: Any
    media_type: str
    original_name: str | None
    mime_type: str | None
    file_size: int | None


@dataclass(slots=True)
class MessageBundle:
    record: dict[str, Any]
    links: list[tuple[int, int, str]] = field(default_factory=list)
    media_row: dict[str, Any] | None = None
    media_job: MediaJob | None = None


def detect_media_type(message: Any) -> str | None:

    checks = (
        ("video_note", "video_note"),
        ("gif", "gif"),
        ("voice", "voice"),
        ("audio", "audio"),
        ("video", "video"),
        ("sticker", "sticker"),
        ("photo", "photo"),
        ("document", "document"),
        ("poll", "poll"),
        ("contact", "contact"),
        ("geo", "geo"),
        ("venue", "venue"),
        ("game", "game"),
        ("invoice", "invoice"),
        ("web_preview", "web_page"),
    )
    for attribute, media_type in checks:
        if getattr(message, attribute, None):
            return media_type
    if getattr(message, "action", None):
        return "service"
    if getattr(message, "media", None):
        return type(message.media).__name__
    return None


def _sender_fields(message: Any) -> tuple[int | None, str | None, str | None]:
    sender_id = getattr(message, "sender_id", None)
    sender = getattr(message, "sender", None)
    if sender is None:
        return sender_id, None, None
    try:
        sender_name = telethon_utils.get_display_name(sender) or None
    except (TypeError, ValueError, AttributeError):
        sender_name = None
    username = getattr(sender, "username", None)
    return sender_id, sender_name, username


def _reply_id(message: Any) -> int | None:
    direct = getattr(message, "reply_to_msg_id", None)
    if direct:
        return int(direct)
    reply_to = getattr(message, "reply_to", None)
    nested = getattr(reply_to, "reply_to_msg_id", None)
    return int(nested) if nested else None


def _media_file_fields(
    message: Any,
    media_type: str | None,
) -> tuple[str | None, str | None, int | None, str | None]:
    file_info = getattr(message, "file", None)
    original_name = getattr(file_info, "name", None) if file_info else None
    mime_type = getattr(file_info, "mime_type", None) if file_info else None
    file_size = getattr(file_info, "size", None) if file_info else None
    if not mime_type and media_type == "photo":
        mime_type = "image/jpeg"

    remote_object = getattr(message, "photo", None) or getattr(message, "document", None)
    remote_id = getattr(remote_object, "id", None)
    return (
        original_name,
        mime_type,
        int(file_size) if file_size is not None else None,
        str(remote_id) if remote_id is not None else None,
    )


def build_message_bundle(
    message: Any,
    chat_id: int,
    chat_slug: str,
    options: ParseOptions,
) -> MessageBundle:
    media_type = detect_media_type(message)
    sender_id, sender_name, sender_username = _sender_fields(message)
    original_name, mime_type, file_size, remote_id = _media_file_fields(
        message, media_type
    )
    replies = getattr(message, "replies", None)
    entities = getattr(message, "entities", None) or []
    text = getattr(message, "message", None) or getattr(message, "raw_text", None)

    raw = tl_dict(message) or {"repr": repr(message)}
    message_id = int(message.id)
    record = {
        "chat_id": chat_id,
        "message_id": message_id,
        "date": utc_iso(getattr(message, "date", None)),
        "edit_date": utc_iso(getattr(message, "edit_date", None)),
        "sender_id": sender_id,
        "sender_name": sender_name,
        "sender_username": sender_username,
        "text": text,
        "message_kind": "service" if getattr(message, "action", None) else "message",
        "media_type": media_type,
        "reply_to_message_id": _reply_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "via_bot_id": getattr(message, "via_bot_id", None),
        "views": getattr(message, "views", None),
        "forwards": getattr(message, "forwards", None),
        "replies_count": getattr(replies, "replies", None),
        "is_outgoing": int(bool(getattr(message, "out", False))),
        "is_pinned": int(bool(getattr(message, "pinned", False))),
        "is_silent": int(bool(getattr(message, "silent", False))),
        "is_post": int(bool(getattr(message, "post", False))),
        "post_author": getattr(message, "post_author", None),
        "forward_json": json_dumps(tl_dict(getattr(message, "fwd_from", None))),
        "reactions_json": json_dumps(tl_dict(getattr(message, "reactions", None))),
        "entities_json": json_dumps([tl_dict(item) for item in entities]),
        "action_json": json_dumps(tl_dict(getattr(message, "action", None))),
        "media_json": json_dumps(tl_dict(getattr(message, "media", None))),
        "raw_json": json_dumps(raw) or "{}",
    }

    links = [(chat_id, message_id, url) for url in extract_urls(text, entities)]
    media_row = None
    media_job = None
    if media_type in DOWNLOADABLE_MEDIA:
        allowed = options.media_allowed(media_type)
        media_row = {
            "chat_id": chat_id,
            "message_id": message_id,
            "media_type": media_type,
            "mime_type": mime_type,
            "original_name": original_name,
            "remote_id": remote_id,
            "file_size": file_size,
            "status": "pending" if allowed else "disabled",
        }
        if allowed:
            media_job = MediaJob(
                chat_id=chat_id,
                chat_slug=chat_slug,
                message_id=message_id,
                date=getattr(message, "date", None),
                message=message,
                media_type=media_type,
                original_name=original_name,
                mime_type=mime_type,
                file_size=file_size,
            )

    return MessageBundle(
        record=record,
        links=links,
        media_row=media_row,
        media_job=media_job,
    )


class TelegramArchiveParser:
    def __init__(
        self,
        settings: Settings,
        database: ArchiveDatabase,
        options: ParseOptions,
    ):
        self.settings = settings
        self.database = database
        self.options = options
        self.client = TelegramClient(
            str(settings.session),
            settings.api_id,
            settings.api_hash,
            flood_sleep_threshold=settings.flood_sleep_threshold,
            request_retries=8,
            connection_retries=8,
            retry_delay=2,
            auto_reconnect=True,
            receive_updates=False,
        )
        self.media_queue: asyncio.Queue[MediaJob | None] = asyncio.Queue(
            maxsize=max(20, settings.media_workers * 10)
        )
        self._media_workers: list[asyncio.Task[None]] = []
        self._global_stats = ParseStats()
        self._stats_lock = asyncio.Lock()
        self._queued_media: set[tuple[int, int]] = set()
        self._queue_lock = asyncio.Lock()

    async def __aenter__(self) -> "TelegramArchiveParser":
        if self.settings.phone:
            await self.client.start(phone=self.settings.phone)
        else:
            await self.client.start()
        await asyncio.to_thread(self.settings.secure_session_files)
        self._media_workers = [
            asyncio.create_task(
                self._media_worker(index + 1),
                name=f"media-worker-{index + 1}",
            )
            for index in range(self.settings.media_workers)
        ]
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        try:
            await self.finish_media()
        finally:
            await self.client.disconnect()

    async def finish_media(self) -> None:
        if not self._media_workers:
            return
        await self.media_queue.join()
        for _ in self._media_workers:
            await self.media_queue.put(None)
        results = await asyncio.gather(*self._media_workers, return_exceptions=True)
        self._media_workers.clear()
        for result in results:
            if isinstance(result, BaseException):
                LOGGER.error("Media worker завершился с ошибкой: %r", result)

    async def resolve_chat(self, reference: str | int | Any) -> Any:
        if isinstance(reference, str):
            reference = normalize_chat_reference(reference)
        return await self.client.get_entity(reference)

    async def list_dialogs(self) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        async for dialog in self.client.iter_dialogs(limit=None):
            entity = dialog.entity
            result.append(
                {
                    "chat_id": telethon_utils.get_peer_id(entity),
                    "title": dialog.name,
                    "username": getattr(entity, "username", None),
                    "kind": self._peer_type(entity),
                    "unread": dialog.unread_count,
                }
            )
        return result

    async def _enqueue_media(self, job: MediaJob) -> bool:
        key = (job.chat_id, job.message_id)
        if not await self.database.should_download_media(*key):
            return False
        async with self._queue_lock:
            if key in self._queued_media:
                return False
            self._queued_media.add(key)
        try:
            await self.media_queue.put(job)
        except BaseException:
            async with self._queue_lock:
                self._queued_media.discard(key)
            raise
        return True

    async def _recover_media_jobs(
        self,
        entity: Any,
        chat_id: int,
        chat_slug: str,
    ) -> int:
        rows = await self.database.list_media_for_recovery(
            chat_id,
            self.options.media_types,
        )
        queued = 0
        for start in range(0, len(rows), MEDIA_FETCH_BATCH):
            chunk = rows[start:start + MEDIA_FETCH_BATCH]
            ids = [int(row["message_id"]) for row in chunk]
            messages = await self.client.get_messages(entity, ids=ids)
            if not isinstance(messages, list):
                messages = [messages]
            by_id = {
                int(message.id): message
                for message in messages
                if message is not None
            }
            for row in chunk:
                message_id = int(row["message_id"])
                message = by_id.get(message_id)
                if message is None:
                    await self.database.update_media_result(
                        chat_id,
                        message_id,
                        status="missing",
                        error="Сообщение недоступно или удалено",
                    )
                    continue
                media_type = str(row["media_type"])
                original_name, mime_type, file_size, _ = _media_file_fields(
                    message, media_type
                )
                job = MediaJob(
                    chat_id=chat_id,
                    chat_slug=chat_slug,
                    message_id=message_id,
                    date=getattr(message, "date", None),
                    message=message,
                    media_type=media_type,
                    original_name=original_name or row.get("original_name"),
                    mime_type=mime_type or row.get("mime_type"),
                    file_size=file_size if file_size is not None else row.get("file_size"),
                )
                if await self._enqueue_media(job):
                    queued += 1
        if queued:
            LOGGER.info("Восстановлено media-задач из БД: %d", queued)
        return queued

    async def parse_one(self, reference: str | int | Any) -> ParseStats:
        entity = (
            await self.resolve_chat(reference)
            if isinstance(reference, (str, int))
            else reference
        )
        chat_id = telethon_utils.get_peer_id(entity)
        chat_title = self._chat_title(entity)
        chat_slug = f"{chat_id}_{sanitize_component(chat_title, 70)}"
        await self.database.upsert_chat(self._chat_record(entity, chat_id))

        stats = ParseStats(chats=1)
        last_id = (
            await self.database.get_last_message_id(chat_id)
            if self.options.resume
            else 0
        )
        LOGGER.info("Парсинг %s [%s], checkpoint=%s", chat_title, chat_id, last_id)

        batch: list[dict[str, Any]] = []
        links: list[tuple[int, int, str]] = []
        media_rows: list[dict[str, Any]] = []
        pending_jobs: list[MediaJob] = []
        recent_ids: set[int] = set()

        async def flush() -> None:
            nonlocal batch, links, media_rows, pending_jobs
            if not batch:
                return
            saved = await self.database.save_batch(batch, links, media_rows)
            stats.messages_saved += saved
            stats.links += len(links)
            for job in pending_jobs:
                if await self._enqueue_media(job):
                    stats.media_queued += 1
            batch, links, media_rows, pending_jobs = [], [], [], []

        async def consume(iterator, *, ascending: bool, deduplicate: bool) -> None:
            async for message in iterator:
                if message is None:
                    continue
                message_id = int(message.id)
                if deduplicate and message_id in recent_ids:
                    continue
                message_date = getattr(message, "date", None)
                if message_date and message_date.tzinfo is None:
                    message_date = message_date.replace(tzinfo=UTC)
                if (
                    self.options.from_date
                    and message_date
                    and message_date < self.options.from_date
                ):
                    if not ascending:
                        break
                    continue
                if self.options.to_date and message_date and message_date > self.options.to_date:
                    if ascending:
                        break
                    continue

                if ascending and self.options.recheck_last > 0:
                    recent_ids.add(message_id)
                    if len(recent_ids) > self.options.recheck_last * 2:
                        threshold = sorted(recent_ids)[-self.options.recheck_last:]
                        recent_ids.clear()
                        recent_ids.update(threshold)

                stats.messages_seen += 1
                bundle = build_message_bundle(
                    message, chat_id, chat_slug, self.options
                )
                batch.append(bundle.record)
                links.extend(bundle.links)
                if bundle.media_row:
                    media_rows.append(bundle.media_row)
                if bundle.media_job:
                    pending_jobs.append(bundle.media_job)
                if len(batch) >= self.settings.db_batch_size:
                    await flush()
                    if stats.messages_seen % 1000 == 0:
                        LOGGER.info(
                            "%s: обработано %d сообщений",
                            chat_title,
                            stats.messages_seen,
                        )

        try:
            if self.options.media_types != frozenset():
                try:
                    stats.media_queued += await self._recover_media_jobs(
                        entity, chat_id, chat_slug
                    )
                except FloodWaitError as exc:
                    stats.errors += 1
                    LOGGER.error(
                        "Не удалось восстановить media для %s: FloodWait %s секунд",
                        chat_title,
                        exc.seconds,
                    )
                except RPCError as exc:
                    stats.errors += 1
                    LOGGER.warning(
                        "Не удалось восстановить media для %s: %s",
                        chat_title,
                        exc,
                    )

            from_offset = (
                self.options.from_date - timedelta(microseconds=1)
                if self.options.from_date
                else None
            )
            iterator = self.client.iter_messages(
                entity,
                limit=self.options.limit,
                min_id=last_id,
                offset_date=from_offset,
                reverse=True,
            )
            await consume(iterator, ascending=True, deduplicate=False)

            if last_id and self.options.recheck_last > 0:
                to_offset = (
                    self.options.to_date + timedelta(microseconds=1)
                    if self.options.to_date
                    else None
                )
                await consume(
                    self.client.iter_messages(
                        entity,
                        limit=self.options.recheck_last,
                        offset_date=to_offset,
                    ),
                    ascending=False,
                    deduplicate=True,
                )
            await flush()
        except asyncio.CancelledError:
            await flush()
            raise
        except FloodWaitError as exc:
            stats.errors += 1
            try:
                await flush()
            except Exception:
                stats.errors += 1
                LOGGER.exception("Не удалось сохранить остаток batch для %s", chat_title)
            LOGGER.error("FloodWait для %s: %s секунд", chat_title, exc.seconds)
        except RPCError as exc:
            stats.errors += 1
            try:
                await flush()
            except Exception:
                stats.errors += 1
                LOGGER.exception("Не удалось сохранить остаток batch для %s", chat_title)
            LOGGER.exception("Telegram RPC error для %s: %s", chat_title, exc)
        except Exception:
            stats.errors += 1
            try:
                await flush()
            except Exception:
                stats.errors += 1
                LOGGER.exception("Не удалось сохранить остаток batch для %s", chat_title)
            LOGGER.exception("Ошибка парсинга %s", chat_title)

        async with self._stats_lock:
            self._global_stats.merge(stats)
        LOGGER.info(
            "Готово %s: сообщений=%d, media в очереди=%d, ошибок=%d",
            chat_title,
            stats.messages_saved,
            stats.media_queued,
            stats.errors,
        )
        return stats

    async def parse_all(self) -> ParseStats:
        queue: asyncio.Queue[Any | None] = asyncio.Queue(
            maxsize=max(10, self.options.chat_workers * 3)
        )
        total = ParseStats()
        total_lock = asyncio.Lock()

        async def worker() -> None:
            while True:
                entity = await queue.get()
                try:
                    if entity is None:
                        return
                    try:
                        stats = await self.parse_one(entity)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        LOGGER.exception("Необработанная ошибка при разборе диалога")
                        stats = ParseStats(chats=1, errors=1)
                    async with total_lock:
                        total.merge(stats)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker(), name=f"chat-worker-{index + 1}")
            for index in range(self.options.chat_workers)
        ]
        try:
            async for dialog in self.client.iter_dialogs(limit=None):
                await queue.put(dialog.entity)
            await queue.join()
        except BaseException:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            raise
        else:
            for _ in workers:
                await queue.put(None)
            await asyncio.gather(*workers)
            return total

    async def _media_worker(self, worker_id: int) -> None:
        while True:
            job = await self.media_queue.get()
            try:
                if job is None:
                    return
                try:
                    await self._download_media(job, worker_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    async with self._stats_lock:
                        self._global_stats.errors += 1
                    LOGGER.exception(
                        "Media worker %d аварийно обработал %s/%s",
                        worker_id,
                        job.chat_id,
                        job.message_id,
                    )
            finally:
                if job is not None:
                    async with self._queue_lock:
                        self._queued_media.discard((job.chat_id, job.message_id))
                self.media_queue.task_done()

    async def _download_media(self, job: MediaJob, worker_id: int) -> None:
        claimed = await self.database.claim_media_download(job.chat_id, job.message_id)
        if not claimed:
            return
        max_bytes = self.settings.max_media_size_mb * 1024 * 1024
        if max_bytes and job.file_size is not None and job.file_size > max_bytes:
            await self.database.update_media_result(
                job.chat_id,
                job.message_id,
                status="skipped_size",
                error=f"Размер {job.file_size} превышает лимит {max_bytes}",
            )
            async with self._stats_lock:
                self._global_stats.media_skipped += 1
            return

        date = job.date
        if date is not None and date.tzinfo is None:
            date = date.replace(tzinfo=UTC)
        date_part = date.astimezone(UTC).strftime("%Y-%m") if date else "unknown-date"
        target_dir = self.settings.media_dir / job.chat_slug / date_part
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)

        extension = safe_extension(job.original_name)
        if not extension:
            extension = {
                "application/x-tgsticker": ".tgs",
                "video/webm": ".webm",
                "image/webp": ".webp",
            }.get(job.mime_type or "", "")
        if not extension:
            extension = {
                "photo": ".jpg",
                "video": ".mp4",
                "video_note": ".mp4",
                "voice": ".ogg",
                "audio": ".mp3",
                "gif": ".mp4",
                "sticker": ".webp",
            }.get(job.media_type, "")
        if not extension and job.mime_type:
            guessed = mimetypes.guess_extension(job.mime_type) or ""
            extension = guessed if len(guessed) <= 11 else ""
        if not extension:
            extension = ".bin"

        descriptive = sanitize_component(
            Path(job.original_name).stem if job.original_name else job.media_type,
            80,
        )
        target = target_dir / f"{job.message_id}_{descriptive}{extension.lower()}"
        temporary = target.with_name(target.name + ".part")

        downloaded_path: Path | None = None
        try:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            downloaded = await job.message.download_media(file=str(temporary))
            if not downloaded:
                raise RuntimeError("Telethon не вернул путь к файлу")
            downloaded_path = Path(downloaded)
            if not await asyncio.to_thread(downloaded_path.is_file):
                raise FileNotFoundError(f"Скачанный файл не найден: {downloaded_path}")

            actual_size = await asyncio.to_thread(lambda: downloaded_path.stat().st_size)
            if actual_size <= 0:
                raise IOError("Telethon создал пустой файл")
            if job.file_size is not None and actual_size != job.file_size:
                raise IOError(
                    f"Размер не совпал: ожидалось {job.file_size}, получено {actual_size}"
                )
            if max_bytes and actual_size > max_bytes:
                await asyncio.to_thread(downloaded_path.unlink, missing_ok=True)
                await self.database.update_media_result(
                    job.chat_id,
                    job.message_id,
                    status="skipped_size",
                    error=f"Фактический размер {actual_size} превышает лимит {max_bytes}",
                )
                async with self._stats_lock:
                    self._global_stats.media_skipped += 1
                return

            if downloaded_path != target:
                await asyncio.to_thread(downloaded_path.replace, target)
            if os.name == "posix":
                await asyncio.to_thread(target.chmod, 0o600)
            await self.database.update_media_result(
                job.chat_id,
                job.message_id,
                status="downloaded",
                local_path=str(target),
            )
            async with self._stats_lock:
                self._global_stats.media_downloaded += 1
            LOGGER.debug("Media worker %d: %s", worker_id, target)
        except Exception as exc:
            await asyncio.to_thread(temporary.unlink, missing_ok=True)
            if downloaded_path is not None and downloaded_path != target:
                await asyncio.to_thread(downloaded_path.unlink, missing_ok=True)
            retry_at = datetime.now(UTC) + timedelta(minutes=15)
            await self.database.update_media_result(
                job.chat_id,
                job.message_id,
                status="error",
                error=f"{type(exc).__name__}: {exc}"[:MAX_ERROR_LENGTH],
                next_retry_at=retry_at.isoformat(),
            )
            async with self._stats_lock:
                self._global_stats.errors += 1
            LOGGER.warning(
                "Не удалось скачать media %s/%s: %s",
                job.chat_id,
                job.message_id,
                exc,
            )

    @staticmethod
    def _chat_title(entity: Any) -> str:
        title = getattr(entity, "title", None)
        if title:
            return title
        full_name = " ".join(
            part
            for part in (
                getattr(entity, "first_name", None),
                getattr(entity, "last_name", None),
            )
            if part
        )
        return (
            full_name
            or getattr(entity, "username", None)
            or str(getattr(entity, "id", "unknown"))
        )

    @staticmethod
    def _peer_type(entity: Any) -> str:
        name = type(entity).__name__.lower()
        if "channel" in name:
            return "channel" if getattr(entity, "broadcast", False) else "supergroup"
        if "chat" in name:
            return "group"
        if "user" in name:
            return "user"
        return type(entity).__name__

    def _chat_record(self, entity: Any, chat_id: int) -> dict[str, Any]:
        return {
            "chat_id": chat_id,
            "raw_id": getattr(entity, "id", None),
            "peer_type": self._peer_type(entity),
            "title": self._chat_title(entity),
            "username": getattr(entity, "username", None),
            "first_name": getattr(entity, "first_name", None),
            "last_name": getattr(entity, "last_name", None),
            "participants_count": getattr(entity, "participants_count", None),
            "is_broadcast": int(bool(getattr(entity, "broadcast", False))),
            "is_megagroup": int(bool(getattr(entity, "megagroup", False))),
            "raw_json": json_dumps(tl_dict(entity)),
            "last_parsed_at": datetime.now(UTC).isoformat(),
        }

    @property
    def global_stats(self) -> ParseStats:
        return self._global_stats
