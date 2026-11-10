from __future__ import annotations

import hashlib
import json
import time
import xml.etree.ElementTree as ET

import pytest

from bot.server import Deduplicator, create_app
from tests.conftest import build_config


def make_app(tmp_path, **overrides):
    cfg = build_config(memory={"path": str(tmp_path / "bot.db")}, **overrides)
    app = create_app(cfg)
    app.testing = True
    return app


class FakeResponse:
    status_code = 200
    text = '{"status":"ok","retcode":0}'

    def json(self):
        return {"status": "ok", "retcode": 0}


class FakeSession:
    def __init__(self):
        self.calls = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse()


PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "self_id": 10000,
    "user_id": 20001,
    "message_id": 42,
    "sender": {"nickname": "小明"},
    "message": [{"type": "text", "data": {"text": "你好"}}],
}


def test_healthz(tmp_path):
    app = make_app(tmp_path)
    resp = app.test_client().get("/healthz")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "ok"
    assert data["model"] == "mock"
    assert data["paused"] is False
    assert data["guard"]["requests_today"] == 0


def test_index_page(tmp_path):
    app = make_app(tmp_path)
    resp = app.test_client().get("/")
    assert resp.status_code == 200
    assert "聊天机器人" in resp.get_data(as_text=True)


def test_onebot_event_replies(tmp_path):
    app = make_app(tmp_path)
    session = FakeSession()
    app.extensions["bot"]["onebot11"]._session = session
    client = app.test_client()

    resp = client.post("/onebot11/event", json=PRIVATE_EVENT)
    assert resp.status_code == 200
    # async_reply=False -> 同步处理完毕后再返回
    assert resp.get_json()["status"] == "ok"

    assert session.calls, "应该调用 OneBot 发送接口"
    url, kwargs = session.calls[0]
    assert url.endswith("/send_private_msg")
    assert "mock 模式" in kwargs["json"]["message"][0]["data"]["text"]
    assert app.extensions["bot"]["memory"].stats()["turns"] == 2


def test_onebot_ignores_unrelated_event(tmp_path):
    app = make_app(tmp_path)
    resp = app.test_client().post("/onebot11/event", json={"post_type": "notice"})
    assert resp.get_json()["status"] == "ignored"


def test_onebot_deduplicates_retries(tmp_path):
    app = make_app(tmp_path)
    app.extensions["bot"]["onebot11"]._session = FakeSession()
    client = app.test_client()
    assert client.post("/onebot11/event", json=PRIVATE_EVENT).get_json()["status"] == "ok"
    assert client.post("/onebot11/event", json=PRIVATE_EVENT).get_json()["status"] == "duplicate"


def test_onebot_rejects_bad_signature(tmp_path):
    app = make_app(tmp_path, onebot11={"enabled": True, "secret": "s3cret", "async_reply": False})
    resp = app.test_client().post("/onebot11/event", json=PRIVATE_EVENT)
    assert resp.status_code == 403

    body = json.dumps(PRIVATE_EVENT).encode()
    sig = "sha1=" + hashlib.sha1(b"wrong").hexdigest()
    resp = app.test_client().post("/onebot11/event", data=body, headers={"X-Signature": sig})
    assert resp.status_code == 403


def test_onebot_accepts_valid_signature(tmp_path):
    import hmac

    app = make_app(tmp_path, onebot11={"enabled": True, "secret": "s3cret", "async_reply": False})
    app.extensions["bot"]["onebot11"]._session = FakeSession()
    body = json.dumps(PRIVATE_EVENT).encode()
    sig = "sha1=" + hmac.new(b"s3cret", body, hashlib.sha1).hexdigest()
    resp = app.test_client().post(
        "/onebot11/event", data=body, headers={"X-Signature": sig, "Content-Type": "application/json"}
    )
    assert resp.status_code == 200
    assert resp.get_json()["status"] == "ok"


def test_onebot_disabled(tmp_path):
    app = make_app(tmp_path, onebot11={"enabled": False})
    assert app.test_client().post("/onebot11/event", json=PRIVATE_EVENT).get_json()["status"] == "disabled"


# ------------------------------------------------------------------ 公众号
def _wechat_query(token: str = "test-token", ts: str = "1700000000", nonce: str = "n1"):
    signature = hashlib.sha1("".join(sorted([token, ts, nonce])).encode()).hexdigest()
    return {"signature": signature, "timestamp": ts, "nonce": nonce, "echostr": "hello"}


WECHAT_XML = """<xml>
<ToUserName><![CDATA[gh_abc123]]></ToUserName>
<FromUserName><![CDATA[oUser_xyz]]></FromUserName>
<CreateTime>1700000000</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[你好]]></Content>
<MsgId>1</MsgId>
</xml>"""


def test_wechat_server_verification(tmp_path):
    app = make_app(tmp_path)
    resp = app.test_client().get("/wechat_mp", query_string=_wechat_query())
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "hello"


def test_wechat_verification_rejects_bad_signature(tmp_path):
    app = make_app(tmp_path)
    q = _wechat_query()
    q["signature"] = "bad"
    assert app.test_client().get("/wechat_mp", query_string=q).status_code == 403


def test_wechat_text_message_replies_xml(tmp_path):
    app = make_app(tmp_path)
    resp = app.test_client().post("/wechat_mp", query_string=_wechat_query(), data=WECHAT_XML)
    assert resp.status_code == 200
    root = ET.fromstring(resp.get_data(as_text=True))
    assert root.findtext("ToUserName") == "oUser_xyz"
    assert root.findtext("MsgType") == "text"
    assert "mock 模式" in root.findtext("Content")
    assert app.extensions["bot"]["memory"].stats()["turns"] == 2


def test_wechat_unsupported_type_is_acked(tmp_path):
    app = make_app(tmp_path)
    xml = WECHAT_XML.replace("<![CDATA[text]]>", "<![CDATA[video]]>")
    resp = app.test_client().post("/wechat_mp", query_string=_wechat_query(), data=xml)
    assert resp.status_code == 200
    assert resp.get_data(as_text=True) == "success"


def test_wechat_image_without_picurl_is_acked(tmp_path):
    app = make_app(tmp_path)
    xml = (
        "<xml><ToUserName>gh</ToUserName><FromUserName>oU</FromUserName>"
        "<MsgType><![CDATA[image]]></MsgType><MsgId>9</MsgId></xml>"
    )
    resp = app.test_client().post("/wechat_mp", query_string=_wechat_query(), data=xml)
    assert resp.get_data(as_text=True) == "success"


def test_wechat_event_is_acked(tmp_path):
    app = make_app(tmp_path)
    xml = (
        "<xml><ToUserName>gh</ToUserName><FromUserName>oU</FromUserName>"
        "<MsgType><![CDATA[event]]></MsgType><Event><![CDATA[subscribe]]></Event></xml>"
    )
    resp = app.test_client().post("/wechat_mp", query_string=_wechat_query(), data=xml)
    assert resp.get_data(as_text=True) == "success"


def test_wechat_timeout_returns_fallback(tmp_path):
    app = make_app(tmp_path, wechat_mp={"enabled": True, "token": "test-token", "reply_timeout": 0.05})

    def slow(_msg):
        time.sleep(0.5)
        return "迟到的答案"

    app.extensions["bot"]["handler"].handle = slow
    resp = app.test_client().post("/wechat_mp", query_string=_wechat_query(), data=WECHAT_XML)
    root = ET.fromstring(resp.get_data(as_text=True))
    assert root.findtext("Content") == app.extensions["bot"]["config"].wechat_mp.timeout_text


# ------------------------------------------------------------------ 去重
def test_deduplicator():
    d = Deduplicator(ttl=60)
    assert not d.is_duplicate("a")
    assert d.is_duplicate("a")
    assert not d.is_duplicate("")
    assert not d.is_duplicate("b")


def test_deduplicator_expires():
    d = Deduplicator(ttl=0.01)
    assert not d.is_duplicate("a")
    time.sleep(0.02)
    assert not d.is_duplicate("a")
