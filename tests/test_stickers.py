from __future__ import annotations

import base64

import pytest

from bot.adapters.onebot11 import OneBot11Client
from bot.models import Message
from bot.server import create_app
from bot.stickers import StickerBox
from tests.conftest import build_config
from tests.test_onebot11 import FakeSession

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def msg(text: str, **kwargs) -> Message:
    data = {
        "platform": "onebot11",
        "chat_type": "private",
        "chat_id": "10001",
        "user_id": "10001",
        "user_name": "小明",
        "text": text,
    }
    data.update(kwargs)
    return Message(**data)


@pytest.fixture
def sticker_dir(tmp_path):
    d = tmp_path / "stickers"
    d.mkdir()
    (d / "a.png").write_bytes(PNG)
    (d / "b.png").write_bytes(PNG)
    (d / "notes.txt").write_text("不是图片，应该被忽略", encoding="utf-8")
    return d


# ------------------------------------------------------------------ 表情包库
def test_box_loads_only_images(sticker_dir):
    box = StickerBox(sticker_dir)
    assert box.count == 2
    assert all(p.suffix == ".png" for p in box.all())


def test_box_missing_dir(tmp_path):
    box = StickerBox(tmp_path / "nope")
    assert box.count == 0
    assert box.random() is None


def test_box_skips_oversize(sticker_dir):
    box = StickerBox(sticker_dir, max_mb=0.000001)
    assert box.count == 0


def test_box_random_avoids_repeat(sticker_dir):
    box = StickerBox(sticker_dir)
    first = box.random()
    second = box.random()
    assert first != second
    assert {first.name, second.name} == {"a.png", "b.png"}


# ------------------------------------------------------------------ 触发发送
def test_trigger_sends_sticker_without_llm(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler._stickers = None

    out = handler.reply(msg("来个表情包"))
    assert out is not None
    assert out.sticker.endswith(".png")
    assert out.text == "呐，给你～"
    assert handler.llm.calls == []


def test_group_trigger_requires_at(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler._stickers = None

    assert handler.reply(msg("来个表情包", chat_type="group", chat_id="555")) is None

    out = handler.reply(msg("来个表情包", chat_type="group", chat_id="555", at_me=True))
    assert out is not None and out.sticker


def test_disabled_sticker_falls_through_to_llm(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler.config.sticker.enabled = False

    out = handler.reply(msg("来个表情包"))
    assert out is not None
    assert out.text == "好的呀"
    assert out.sticker == ""


# ------------------------------------------------------------------ 概率附带
def test_probability_one_attaches_sticker(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler._stickers = None
    handler.config.sticker.probability = 1.0

    out = handler.reply(msg("你好"))
    assert out.text == "好的呀"
    assert out.sticker.endswith(".png")


def test_probability_zero_never_attaches(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler._stickers = None
    handler.config.sticker.probability = 0.0

    out = handler.reply(msg("你好"))
    assert out.text == "好的呀"
    assert out.sticker == ""


def test_group_probability_can_be_disabled(handler, sticker_dir):
    handler.config.sticker.dir = str(sticker_dir)
    handler._stickers = None
    handler.config.sticker.probability = 1.0
    handler.config.sticker.group_enabled = False

    out = handler.reply(msg("你好", chat_type="group", chat_id="555", at_me=True))
    assert out.text == "好的呀"
    assert out.sticker == ""


# ------------------------------------------------------------------ 发送
def test_send_image_segment(sticker_dir):
    session = FakeSession()
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)

    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_image(m, str(sticker_dir / "a.png")) is True

    url, kwargs = session.calls[0]
    assert url.endswith("/send_private_msg")
    seg = kwargs["json"]["message"][0]
    assert seg["type"] == "image"
    assert seg["data"]["file"].startswith("file:///")


def test_send_image_bad_path():
    session = FakeSession()
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)
    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_image(m, "") is False
    assert client.send_image(m, "C:/no/such/file.png") is False


class _SeqSession:
    """按顺序返回不同响应，用来测 file:// 失败后回退 base64。"""

    def __init__(self, responses):
        self.calls = []
        self._responses = list(responses)

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self._responses.pop(0)


class _Resp:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "ok", "retcode": 0}
        self.text = "{}"

    def json(self):
        return self._payload


def test_send_image_falls_back_to_base64(tmp_path):
    png = tmp_path / "a.png"
    png.write_bytes(PNG)
    session = _SeqSession([_Resp(payload={"status": "failed", "retcode": 100}), _Resp()])
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)

    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_image(m, str(png)) is True
    assert len(session.calls) == 2
    second = session.calls[1][1]["json"]["message"][0]
    assert second["data"]["file"].startswith("base64://")


def test_server_sends_text_then_sticker(tmp_path, sticker_dir):
    cfg = build_config(
        memory={"path": str(tmp_path / "bot.db")},
        sticker={"dir": str(sticker_dir), "probability": 0},
    )
    app = create_app(cfg)
    app.testing = True

    session = FakeSession()
    app.extensions["bot"]["onebot11"]._session = session

    event = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10000,
        "user_id": 20001,
        "message_id": 321,
        "sender": {"nickname": "小明"},
        "message": [{"type": "text", "data": {"text": "来个表情包"}}],
    }
    resp = app.test_client().post("/onebot11/event", json=event)
    assert resp.get_json()["status"] == "ok"

    assert len(session.calls) == 2
    text_seg = session.calls[0][1]["json"]["message"][0]
    image_seg = session.calls[1][1]["json"]["message"][0]
    assert text_seg["type"] == "text"
    assert image_seg["type"] == "image"
