from datetime import UTC, datetime
from types import SimpleNamespace

from tg_parser.parser import ParseOptions, build_message_bundle, detect_media_type
from tg_parser.utils import extract_urls, normalize_chat_reference, sanitize_component


class FakeTL:
    def __init__(self, **values):
        self.__dict__.update(values)

    def to_dict(self):
        return dict(self.__dict__)


def test_specialized_document_is_not_classified_twice():
    message = SimpleNamespace(
        video=object(), video_note=None, gif=None, voice=None, audio=None,
        sticker=None, photo=None, document=object(), poll=None, contact=None,
        geo=None, venue=None, game=None, invoice=None, web_preview=None,
        action=None, media=object(),
    )
    assert detect_media_type(message) == "video"


def test_message_bundle_extracts_complete_core_fields():
    sender = SimpleNamespace(first_name="Иван", last_name="Иванов", username="ivan")
    message = FakeTL(
        id=42,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        edit_date=None,
        sender_id=7,
        sender=sender,
        message="Текст https://example.com/a",
        raw_text="Текст https://example.com/a",
        entities=[],
        action=None,
        media=None,
        video_note=None,
        gif=None,
        voice=None,
        audio=None,
        video=None,
        sticker=None,
        photo=None,
        document=None,
        poll=None,
        contact=None,
        geo=None,
        venue=None,
        game=None,
        invoice=None,
        web_preview=None,
        replies=None,
        reply_to_msg_id=5,
        reply_to=None,
        grouped_id=None,
        via_bot_id=None,
        views=10,
        forwards=2,
        out=False,
        pinned=False,
        silent=False,
        post=False,
        post_author=None,
        fwd_from=None,
        reactions=None,
        file=None,
    )
    bundle = build_message_bundle(message, -1001, "chat", ParseOptions(download_media="none"))
    assert bundle.record["message_id"] == 42
    assert bundle.record["reply_to_message_id"] == 5
    assert bundle.record["sender_username"] == "ivan"
    assert bundle.links == [(-1001, 42, "https://example.com/a")]
    assert bundle.media_job is None


def test_utilities():
    assert normalize_chat_reference("https://t.me/example?single") == "example"
    assert normalize_chat_reference("-100123") == -100123
    assert sanitize_component('a:b/c*?') == "a_b_c"
    assert extract_urls("a https://example.org/test, b") == ["https://example.org/test"]
