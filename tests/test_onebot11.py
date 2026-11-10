from __future__ import annotations

import hashlib
import hmac
import json

from bot.adapters.onebot11 import OneBot11Client, parse_event, verify_signature
from bot.models import Message
from tests.conftest import build_config


def event(**overrides):
    payload = {
        "post_type": "message",
        "message_type": "private",
        "self_id": 10000,
        "user_id": 20001,
        "message_id": 42,
        "sender": {"nickname": "小明"},
        "message": [{"type": "text", "data": {"text": "你好"}}],
    }
    payload.update(overrides)
    return payload


# ------------------------------------------------------------------ 解析
def test_parse_private_text():
    m = parse_event(event())
    assert m is not None
    assert m.chat_type == "private"
    assert m.chat_id == "20001"
    assert m.text == "你好"
    assert m.at_me is False
    assert m.message_id == "42"
    assert m.session_key == "onebot11:private:20001"


def test_parse_group_with_at_me():
    payload = event(
        message_type="group",
        group_id=555,
        message=[
            {"type": "at", "data": {"qq": "10000"}},
            {"type": "text", "data": {"text": " 讲个笑话"}},
        ],
    )
    m = parse_event(payload)
    assert m.chat_type == "group"
    assert m.chat_id == "555"
    assert m.at_me is True
    assert m.text == "讲个笑话"


def test_parse_group_without_at_is_not_at_me():
    payload = event(
        message_type="group",
        group_id=555,
        message=[
            {"type": "at", "data": {"qq": "99999"}},
            {"type": "text", "data": {"text": "你好"}},
        ],
    )
    m = parse_event(payload)
    assert m.at_me is False
    assert m.text == "你好"


def test_parse_string_message_with_cq_codes():
    m = parse_event(
        event(message="[CQ:at,qq=10000] 你好[CQ:image,file=a.jpg]", raw_message="")
    )
    assert m.at_me is True
    assert m.text == "你好[图片]"


def test_parse_string_message_falls_back_to_raw():
    m = parse_event(event(message="", raw_message="看看这张图[CQ:image,file=b.jpg]"))
    assert m.text == "看看这张图[图片]"


def test_parse_uses_card_name_first():
    m = parse_event(event(sender={"nickname": "昵称", "card": "群名片"}))
    assert m.user_name == "群名片"


def test_parse_ignores_non_message_events():
    assert parse_event({"post_type": "notice", "notice_type": "group_increase"}) is None
    assert parse_event({"post_type": "message", "message_type": "guild"}) is None
    assert parse_event({}) is None
    assert parse_event(event(message_type="message_sent")) is None


def test_ignores_self_message():
    """机器人自己发的消息不能回，否则会自问自答死循环。"""
    payload = event(self_id=10000, user_id=10000, message_type="private")
    assert parse_event(payload) is None


def test_parse_custom_self_ids():
    payload = event(
        self_id=1,
        message_type="group",
        group_id=9,
        message=[{"type": "at", "data": {"qq": "777777"}}],
    )
    assert parse_event(payload, {"777777"}).at_me is True


# ------------------------------------------------------------------ 签名
def test_verify_signature():
    body = b'{"post_type":"message"}'
    secret = "s3cret"
    sig = "sha1=" + hmac.new(secret.encode(), body, hashlib.sha1).hexdigest()
    assert verify_signature(secret, body, sig)
    assert not verify_signature(secret, body, "sha1=deadbeef")
    assert not verify_signature(secret, body, "")


def test_verify_signature_disabled_without_secret():
    assert verify_signature("", b"anything", "")


# ------------------------------------------------------------------ 发送
class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"status": "ok", "retcode": 0}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response=None):
        self.calls = []
        self.response = response or FakeResponse()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.response


def make_client(session):
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000/"})
    return OneBot11Client(cfg.onebot11, session=session)


def test_send_group_message():
    session = FakeSession()
    client = make_client(session)
    m = Message(platform="onebot11", chat_type="group", chat_id="555", user_id="20001")
    assert client.send_text(m, "回答") is True

    url, kwargs = session.calls[0]
    assert url == "http://127.0.0.1:3000/send_group_msg"
    assert kwargs["json"]["group_id"] == 555
    assert kwargs["json"]["message"] == [{"type": "text", "data": {"text": "回答"}}]


def test_send_private_message():
    session = FakeSession()
    client = make_client(session)
    m = Message(platform="onebot11", chat_type="private", chat_id="20001", user_id="20001")
    assert client.send_text(m, "回答") is True
    url, kwargs = session.calls[0]
    assert url.endswith("/send_private_msg")
    assert kwargs["json"]["user_id"] == 20001


def test_send_reports_failure_on_retcode():
    session = FakeSession(FakeResponse(payload={"status": "failed", "retcode": 100}))
    client = make_client(session)
    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_text(m, "回答") is False


def test_send_reports_failure_on_http_error():
    session = FakeSession(FakeResponse(status_code=500))
    client = make_client(session)
    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_text(m, "回答") is False
