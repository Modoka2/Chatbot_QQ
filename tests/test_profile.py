from __future__ import annotations

import pytest

from bot.config import AppConfig
from bot.models import Message
from bot.profile import ProfileExtractor, ProfileStore
from tests.conftest import build_config


@pytest.fixture
def store(tmp_path):
    s = ProfileStore(tmp_path / "profile.db")
    yield s
    s.close()


class FakeClient:
    def __init__(self, reply: str = "阿伟，做跨境电商"):
        self.reply = reply
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return self.reply


def test_store_set_get_persist(tmp_path):
    path = tmp_path / "p.db"
    first = ProfileStore(path)
    first.set("qq:100", "阿伟，做跨境电商")
    first.close()

    second = ProfileStore(path)
    assert second.get("qq:100") == "阿伟，做跨境电商"
    assert second.count() == 1
    second.close()


def test_store_clear(store):
    store.set("qq:100", "内容")
    assert store.clear("qq:100") == 1
    assert store.get("qq:100") == ""


def test_store_missing_returns_empty(store):
    assert store.get("qq:nobody") == ""


def test_extractor_prompt_contains_previous_and_dialogue(tmp_path):
    cfg = build_config()
    client = FakeClient("阿伟，跨境电商；养猫年糕")
    extractor = ProfileExtractor(cfg, client=client)

    result = extractor.update("阿伟，做电商", "用户：我叫阿伟\n机器人：记住啦")
    assert result == "阿伟，跨境电商；养猫年糕"
    prompt = client.calls[0][0]["content"]
    assert "阿伟，做电商" in prompt
    assert "我叫阿伟" in prompt
    assert "不编造" in prompt


def test_extractor_truncates(tmp_path):
    cfg = build_config(profile={"max_chars": 10})
    extractor = ProfileExtractor(cfg, client=FakeClient("字" * 50))
    assert len(extractor.update("", "对话")) == 10


# ------------------------------------------------------------------ 接入聊天
def _msg(text: str) -> Message:
    return Message(
        platform="onebot11", chat_type="private", chat_id="10001",
        user_id="10001", user_name="小明", text=text,
    )


def test_profile_injected_into_prompt(handler):
    handler.config.profile.enabled = True
    handler.profiles().set("onebot11:10001", "阿伟，做跨境电商；养猫年糕")
    handler.handle(_msg("你好"))
    system = handler.llm.calls[-1][0]["content"]
    assert "[用户画像]" in system
    assert "年糕" in system


def test_no_profile_line_when_empty(handler):
    handler.config.profile.enabled = True
    handler.handle(_msg("你好"))
    assert "[用户画像]" not in handler.llm.calls[-1][0]["content"]


def test_profile_command(handler):
    handler.config.profile.enabled = True
    handler.profiles().set("onebot11:10001", "阿伟，做跨境电商")
    assert "跨境电商" in handler.handle(_msg("/画像"))


def test_profile_command_empty(handler):
    handler.config.profile.enabled = True
    assert "还没记住" in handler.handle(_msg("/画像"))


def test_update_worker_saves_profile(handler):
    handler.config.profile.enabled = True

    class FakeProfiler:
        def update(self, previous, dialogue):
            assert "我叫阿伟" in dialogue
            return "阿伟，做跨境电商"

    handler._profiler = FakeProfiler()
    handler.memory.append_turn("onebot11:private:10001", "我叫阿伟，做电商", "记住啦")
    handler._update_profile_worker(_msg("我叫阿伟"))
    assert handler.profiles().get("onebot11:10001") == "阿伟，做跨境电商"
