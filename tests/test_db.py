@@ -57,118 +57,173 @@ class ArchiveDatabaseTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_deleted_message_is_preserved_and_media_marked_missing(self) -> None:
        async with ArchiveDatabase(self.path) as database:
            await database.upsert_chat(chat_record())
            message = message_record()
            message["media_type"] = "photo"
            await database.save_batch(
                [message],
                [],
                [
                    {
                        "chat_id": -1001,
                        "message_id": 1,
                        "media_type": "photo",
                        "mime_type": "image/jpeg",
                        "original_name": None,
                        "remote_id": "123",
                        "file_size": 10,
                        "status": "pending",
                    }
                ],
            )

            marked = await database.mark_messages_deleted(-1001, [1, 1, 999])

            self.assertEqual(marked, 1)
            row = database.conn.execute(
                """
                SELECT m.is_deleted, m.deleted_at, media.status, media.error
                FROM messages m
                JOIN media ON media.chat_id=m.chat_id AND media.message_id=m.message_id
                WHERE m.chat_id=-1001 AND m.message_id=1
                """
            ).fetchone()
            self.assertEqual(row["is_deleted"], 1)
            self.assertIsInstance(row["deleted_at"], str)
            self.assertEqual(row["status"], "missing")
            self.assertIn("удалено", row["error"])

            # Повторное появление сообщения после recheck снимает deletion flag.
            await database.save_batch([message_record(text="restored")], [], [])
            row = database.conn.execute(
                """
                SELECT is_deleted, deleted_at, text
                FROM messages
                WHERE chat_id=-1001 AND message_id=1
                """
            ).fetchone()
            self.assertEqual(tuple(row), (0, None, "restored"))

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
