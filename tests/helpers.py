from __future__ import annotations

from typing import Any


def chat_record(chat_id: int = -1001) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "raw_id": 1,
        "peer_type": "channel",
        "title": "Test chat",
        "username": "test_chat",
        "first_name": None,
        "last_name": None,
        "participants_count": 10,
        "is_broadcast": 1,
        "is_megagroup": 0,
        "raw_json": "{}",
        "last_parsed_at": "2026-07-23T00:00:00+00:00",
    }


def message_record(
    chat_id: int = -1001,
    message_id: int = 1,
    text: str = "hello",
) -> dict[str, Any]:
    return {
        "chat_id": chat_id,
        "message_id": message_id,
        "date": "2026-07-23T00:00:00+00:00",
        "edit_date": None,
        "sender_id": 42,
        "sender_name": "Tester",
        "sender_username": "tester",
        "text": text,
        "message_kind": "message",
        "media_type": None,
        "reply_to_message_id": None,
        "grouped_id": None,
        "via_bot_id": None,
        "views": 1,
        "forwards": 0,
        "replies_count": 0,
        "is_outgoing": 0,
        "is_pinned": 0,
        "is_silent": 0,
        "is_post": 1,
        "post_author": None,
        "forward_json": None,
        "reactions_json": None,
        "entities_json": "[]",
        "action_json": None,
        "media_json": None,
        "raw_json": "{}",
    }
