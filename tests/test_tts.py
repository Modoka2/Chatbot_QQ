from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from bot.adapters.onebot11 import OneBot11Client
from bot.models import Message
from bot.tts import TTSClient, TTSError
from tests.conftest import build_config
from tests.test_onebot11 import FakeSession
from tests.test_stickers import _Resp, _SeqSession


def make_msg(text: str) -> Message:
    return Message(
        platform="onebot11", chat_type="private", chat_id="10001",
        user_id="10001", user_name="小明", text=text,
    )


@pytest.fixture
def tts_client(tmp_path):
    cfg = build_config(tts={"enabled": True, "dir": str(tmp_path / "tts")})
    return TTSClient(cfg.tts)


# ------------------------------------------------------------------ TTS 客户端
def test_synthesize_empty_raises(tts_client):
    with pytest.raises(TTSError):
        tts_client.synthesize("   ")


def test_cleanup_removes_old_files(tts_client):
    old = Path(tts_client.dir) / "old.mp3"
    old.write_bytes(b"x" * 2048)
    stamp = time.time() - 3600
    os.utime(old, (stamp, stamp))

    fresh = Path(tts_client.dir) / "fresh.mp3"
    fresh.write_bytes(b"x" * 2048)

    assert tts_client.cleanup(keep_seconds=60) == 1
    assert not old.exists()
    assert fresh.exists()


def test_cleanup_ignores_non_audio(tts_client):
    other = Path(tts_client.dir) / "note.txt"
    other.write_bytes(b"x")
    stamp = time.time() - 3600
    os.utime(other, (stamp, stamp))
    assert tts_client.cleanup(keep_seconds=60) == 0


class FakeTTS:
    def __init__(self, path: Path):
        self.path = path
        self.calls: list[str] = []
        self.path.write_bytes(b"x" * 2048)

    def synthesize(self, text: str) -> Path:
        self.calls.append(text)
        return self.path

    def cleanup(self, keep_seconds: float = 600) -> int:
        return 0


# ------------------------------------------------------------------ 接入聊天
def test_voice_trigger_produces_voice(handler, tmp_path):
    handler.config.tts.enabled = True
    fake = FakeTTS(tmp_path / "v.mp3")
    handler._tts = fake

    out = handler.reply(make_msg("用语音说句晚安"))
    assert out.voice == str(fake.path)
    assert fake.calls


def test_no_voice_without_trigger(handler, tmp_path):
    handler.config.tts.enabled = True
    handler.config.tts.probability = 0
    handler._tts = FakeTTS(tmp_path / "v.mp3")

    out = handler.reply(make_msg("你好"))
    assert out.voice == ""


def test_long_reply_not_spoken(handler, tmp_path):
    handler.config.tts.enabled = True
    handler.config.tts.max_chars = 5
    handler.llm.reply = "这是一段特别长的回复内容"
    handler._tts = FakeTTS(tmp_path / "v.mp3")

    out = handler.reply(make_msg("用语音说"))
    assert out.voice == ""


def test_code_reply_not_spoken(handler, tmp_path):
    handler.config.tts.enabled = True
    handler.llm.reply = "看这里\n```py\nprint(1)\n```"
    handler._tts = FakeTTS(tmp_path / "v.mp3")

    out = handler.reply(make_msg("用语音说"))
    assert out.voice == ""


def test_tts_failure_falls_back_to_text(handler):
    handler.config.tts.enabled = True

    class Boom:
        def synthesize(self, text):
            raise RuntimeError("合成挂了")

        def cleanup(self, keep_seconds=600):
            return 0

    handler._tts = Boom()
    out = handler.reply(make_msg("用语音说"))
    assert out.text == "好的呀"
    assert out.voice == ""


def test_tts_disabled(handler, tmp_path):
    handler.config.tts.enabled = False
    handler._tts = FakeTTS(tmp_path / "v.mp3")
    out = handler.reply(make_msg("用语音说"))
    assert out.voice == ""


# ------------------------------------------------------------------ 发送
def test_send_voice_segment(tmp_path):
    p = tmp_path / "v.mp3"
    p.write_bytes(b"x" * 2048)
    session = FakeSession()
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)

    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_voice(m, str(p)) is True

    seg = session.calls[0][1]["json"]["message"][0]
    assert seg["type"] == "record"
    assert seg["data"]["file"].startswith("file:///")


def test_send_voice_missing_file():
    session = FakeSession()
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)
    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_voice(m, "C:/no/such.mp3") is False


def test_send_voice_falls_back_to_base64(tmp_path):
    p = tmp_path / "v.mp3"
    p.write_bytes(b"x" * 2048)
    session = _SeqSession([_Resp(payload={"status": "failed", "retcode": 100}), _Resp()])
    cfg = build_config(onebot11={"enabled": True, "api_base": "http://127.0.0.1:3000"})
    client = OneBot11Client(cfg.onebot11, session=session)

    m = Message(platform="onebot11", chat_type="private", chat_id="1", user_id="1")
    assert client.send_voice(m, str(p)) is True
    second = session.calls[1][1]["json"]["message"][0]
    assert second["data"]["file"].startswith("base64://")


# ------------------------------------------------------------------ OpenAI 兼容引擎
class FakeHTTP:
    def __init__(self, content: bytes = b"x" * 4096, status: int = 200, text: str = "err"):
        self.content = content
        self.status_code = status
        self.text = text
        self.calls: list[tuple] = []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self


def make_openai_client(tmp_path, **overrides):
    cfg = build_config(tts={"enabled": True, "dir": str(tmp_path / "tts"), "engine": "openai", **overrides})
    http = FakeHTTP()
    return TTSClient(cfg.tts, session=http), http


def test_openai_engine_success(tmp_path):
    client, http = make_openai_client(
        tmp_path, base_url="https://api.example.com/v1", api_key="k-1", voice="nova"
    )
    path = client.synthesize("你好呀")
    assert path.exists() and path.suffix == ".mp3"

    url, kwargs = http.calls[0]
    assert url == "https://api.example.com/v1/audio/speech"
    assert kwargs["json"]["voice"] == "nova"
    assert kwargs["json"]["input"] == "你好呀"
    assert kwargs["headers"]["Authorization"] == "Bearer k-1"


def test_openai_requires_base_url(tmp_path):
    client, _ = make_openai_client(tmp_path)
    with pytest.raises(TTSError):
        client._openai("你好")


def test_openai_http_error(tmp_path):
    cfg = build_config(tts={"enabled": True, "dir": str(tmp_path / "tts"), "engine": "openai",
                            "base_url": "https://api.example.com/v1"})
    client = TTSClient(cfg.tts, session=FakeHTTP(status=401, text="bad key"))
    with pytest.raises(TTSError):
        client._openai("你好")


def test_online_engine_falls_back_to_sapi(tmp_path, monkeypatch):
    cfg = build_config(tts={"enabled": True, "dir": str(tmp_path / "tts"), "engine": "edge"})
    client = TTSClient(cfg.tts)

    def boom(text):
        raise RuntimeError("网络挂了")

    fallback = tmp_path / "fallback.wav"
    fallback.write_bytes(b"x" * 2048)
    monkeypatch.setattr(client, "_edge", boom)
    monkeypatch.setattr(client, "_sapi", lambda text: fallback)
    assert client.synthesize("你好") == fallback
