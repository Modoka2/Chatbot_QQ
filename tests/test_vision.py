from __future__ import annotations

import base64

import pytest

from bot.adapters.onebot11 import parse_event
from bot.adapters.wechat_mp import parse_message
from bot.handler import ChatHandler
from bot.models import Message
from bot.vision import ImageError, load_image, sniff_mime
from tests.conftest import FakeLLM

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def png_file(tmp_path):
    path = tmp_path / "test.png"
    path.write_bytes(PNG_1PX)
    return path


# ------------------------------------------------------------------ 解析
def test_parse_onebot_image_segment():
    payload = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10000,
        "user_id": 20001,
        "message_id": 7,
        "sender": {"nickname": "小明"},
        "message": [
            {"type": "image", "data": {"file": "abc.jpg", "url": "https://gchat.qpic.cn/a.jpg"}},
        ],
    }
    m = parse_event(payload)
    assert m.images == ["https://gchat.qpic.cn/a.jpg"]
    assert m.text == "[图片]"


def test_parse_onebot_image_in_group_with_text():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "group_id": 555,
        "self_id": 10000,
        "user_id": 20002,
        "message": [
            {"type": "at", "data": {"qq": "10000"}},
            {"type": "image", "data": {"file": "/tmp/x.png", "url": ""}},
            {"type": "text", "data": {"text": " 这图里是啥"}},
        ],
    }
    m = parse_event(payload)
    assert m.at_me is True
    assert m.images == ["/tmp/x.png"]  # 没有 url 时退回 file
    assert m.text == "[图片] 这图里是啥"


def test_parse_onebot_image_cq_string():
    m = parse_event(
        {
            "post_type": "message",
            "message_type": "private",
            "self_id": 10000,
            "user_id": 20001,
            "message": "[CQ:image,file=a.jpg,url=https://x.com/a.jpg]",
        }
    )
    assert m.images == ["https://x.com/a.jpg"]


def test_parse_wechat_image_message():
    xml = (
        "<xml><ToUserName>gh</ToUserName><FromUserName>oU</FromUserName>"
        "<MsgType>image</MsgType><PicUrl><![CDATA[https://mmbiz.qpic.cn/x.jpg]]></PicUrl>"
        "<MsgId>1</MsgId></xml>"
    )
    incoming = parse_message(xml)
    assert incoming.msg_type == "image"
    assert incoming.to_message().images == ["https://mmbiz.qpic.cn/x.jpg"]


# ------------------------------------------------------------------ 读取
def test_load_local_image(png_file):
    data, mime = load_image(str(png_file))
    assert data == PNG_1PX
    assert mime == "image/png"


def test_load_image_size_limit(png_file):
    with pytest.raises(ImageError):
        load_image(str(png_file), max_mb=0.000001)


def test_load_non_image_file(tmp_path):
    fake = tmp_path / "a.png"
    fake.write_bytes(b"not an image")
    with pytest.raises(ImageError):
        load_image(str(fake))


def test_load_unsupported_scheme():
    with pytest.raises(ImageError):
        load_image("ftp://example.com/a.png")


def test_sniff_mime():
    assert sniff_mime(PNG_1PX) == "image/png"
    assert sniff_mime(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff_mime(b"hello") is None


# ------------------------------------------------------------------ 处理
def _msg(**kwargs) -> Message:
    data = {
        "platform": "onebot11",
        "chat_type": "private",
        "chat_id": "10001",
        "user_id": "10001",
        "user_name": "小明",
        "text": "",
    }
    data.update(kwargs)
    return Message(**data)


def test_handler_vision_reply(handler, png_file):
    handler._vision = FakeLLM(reply="图里是一个像素点")
    reply = handler.handle(_msg(images=[str(png_file)]))
    assert reply == "图里是一个像素点"

    parts = handler._vision.calls[-1][-1]["content"]
    assert isinstance(parts, list)
    assert parts[0]["type"] == "text"
    assert parts[1]["type"] == "image_url"
    assert parts[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_handler_vision_uses_caption(handler, png_file):
    handler._vision = FakeLLM(reply="好的")
    handler.handle(_msg(images=[str(png_file)], text="这张图是什么"))
    text_part = handler._vision.calls[-1][-1]["content"][0]["text"]
    assert text_part == "这张图是什么"


def test_handler_vision_disabled(handler, png_file):
    handler.config.vision.enabled = False
    reply = handler.handle(_msg(images=[str(png_file)]))
    assert reply == "识图没开"


def test_handler_vision_unreadable_image(handler):
    handler._vision = FakeLLM(reply="不该被调用")
    reply = handler.handle(_msg(images=["/no/such/file.png"]))
    assert reply == "识图没开"
    assert handler._vision.calls == []


def test_handler_vision_memory_records_image(handler, png_file):
    handler._vision = FakeLLM(reply="看到了")
    handler.handle(_msg(images=[str(png_file)]))
    history = handler.memory.load("onebot11:private:10001")
    assert history[0]["content"].startswith("[图片]")
    assert history[1]["content"] == "看到了"
