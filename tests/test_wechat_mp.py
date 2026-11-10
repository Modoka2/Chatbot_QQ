from __future__ import annotations

import hashlib
import xml.etree.ElementTree as ET

from bot.adapters.wechat_mp import (
    build_text_reply,
    check_signature,
    parse_message,
)

TOKEN = "test-token"


def sign(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()


TEXT_XML = """<xml>
<ToUserName><![CDATA[gh_abc123]]></ToUserName>
<FromUserName><![CDATA[oUser_xyz]]></FromUserName>
<CreateTime>1700000000</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[你好呀]]></Content>
<MsgId>1234567890</MsgId>
</xml>"""


def test_check_signature_ok():
    ts, nonce = "1700000000", "abc"
    assert check_signature(TOKEN, sign(TOKEN, ts, nonce), ts, nonce)


def test_check_signature_bad():
    ts, nonce = "1700000000", "abc"
    assert not check_signature(TOKEN, "deadbeef", ts, nonce)
    assert not check_signature(TOKEN, "", ts, nonce)
    assert not check_signature(TOKEN, sign("other-token", ts, nonce), ts, nonce)


def test_check_signature_disabled_without_token():
    assert check_signature("", "", "", "")


def test_parse_text_message():
    incoming = parse_message(TEXT_XML)
    assert incoming is not None
    assert incoming.from_user == "oUser_xyz"
    assert incoming.to_user == "gh_abc123"
    assert incoming.msg_type == "text"
    assert incoming.content == "你好呀"
    assert incoming.create_time == 1700000000

    m = incoming.to_message()
    assert m.platform == "wechat_mp"
    assert m.chat_type == "private"
    assert m.chat_id == "oUser_xyz"
    assert m.session_key == "wechat_mp:private:oUser_xyz"


def test_parse_voice_uses_recognition():
    xml = (
        "<xml><ToUserName>gh</ToUserName><FromUserName>oU</FromUserName>"
        "<MsgType>voice</MsgType><Recognition><![CDATA[今天天气怎么样]]></Recognition></xml>"
    )
    incoming = parse_message(xml)
    assert incoming.msg_type == "voice"
    assert incoming.to_message().text == "今天天气怎么样"


def test_parse_encrypted_message_is_rejected():
    xml = (
        "<xml><ToUserName>gh</ToUserName><Encrypt><![CDATA[abc]]></Encrypt>"
        "<MsgType><![CDATA[text]]></MsgType></xml>"
    )
    assert parse_message(xml) is None


def test_parse_broken_xml_returns_none():
    assert parse_message("<xml><ToUserName>") is None
    assert parse_message(b"") is None


def test_build_text_reply_roundtrip():
    xml = build_text_reply("oUser_xyz", "gh_abc123", "你好，我是机器人", create_time=1700000001)
    root = ET.fromstring(xml)
    assert root.findtext("ToUserName") == "oUser_xyz"
    assert root.findtext("FromUserName") == "gh_abc123"
    assert root.findtext("MsgType") == "text"
    assert root.findtext("Content") == "你好，我是机器人"
    assert root.findtext("CreateTime") == "1700000001"


def test_build_text_reply_escapes_cdata_end():
    xml = build_text_reply("u", "g", "危险]]>内容")
    root = ET.fromstring(xml)
    assert root.findtext("Content") == "危险]]>内容"


def test_build_text_reply_truncates():
    xml = build_text_reply("u", "g", "字" * 3000, create_time=1)
    assert len(ET.fromstring(xml).findtext("Content")) == 2000
