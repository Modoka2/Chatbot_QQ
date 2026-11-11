"""测试夹具：一份可覆盖的默认配置 + 假模型。"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from bot.config import AppConfig
from bot.handler import ChatHandler
from bot.memory import ConversationMemory

BASE: dict[str, Any] = {
    "llm": {
        "provider": "mock",
        "model": "mock",
        "api_key": "test-key",
        "history_window": 16,
        "system_prompt": "你是测试助手。",
        "daily_token_budget": 0,
        "token_budget_notice": "额度用完了",
    },
    "memory": {
        "path": ":memory:",
        "ttl_minutes": 0,
        "max_turns": 24,
        "summary": {"enabled": False},
    },
    "reply": {
        "private_enabled": True,
        "group_enabled": True,
        "group_require_at": True,
        "group_prefixes": ["/ai"],
        "command_prefix": "/",
        "max_reply_chars": 0,
        "max_input_chars": 0,
        "unsupported_hint": "只看得懂文字",
        "image_disabled_hint": "识图没开",
        "rate_limit": {
            "per_user_per_minute": 0,
            "per_chat_per_minute": 0,
            "per_user_per_day": 0,
            "per_chat_per_day": 0,
            "per_day_total": 0,
            "cooldown_seconds": 0,
            "notice_cooldown_minutes": 3,
            "notice": "限流了",
        },
    },
    "whitelist": {
        "user_ids": [],
        "group_ids": [],
        "admin_ids": ["10001"],
        "reject_notice": "",
    },
    "owner": {"ids": [], "title": "主人", "guest_title": "客人"},
    "vision": {
        "enabled": True,
        "model": "mock-vl",
        "base_url": "",
        "api_key": "",
        "max_images": 3,
        "max_mb": 8,
        "timeout": 10,
        "prompt": "看看这张图",
    },
    "sticker": {
        "enabled": True,
        "dir": "stickers",
        "probability": 0,
        "group_enabled": True,
        "private_enabled": True,
        "max_mb": 5,
        "trigger_keywords": ["表情包"],
        "trigger_text": "呐，给你～",
    },
    "search": {
        "enabled": False,
        "provider": "auto",
        "max_results": 5,
        "timeout": 5,
        "max_snippet": 200,
        "auto_keywords": ["搜一下", "搜索"],
        "searxng_url": "",
        "tavily_key": "",
    },
    "affinity": {"enabled": True},
    "profile": {"enabled": False},
    "tts": {"enabled": False},
    "onebot11": {"enabled": True, "async_reply": False},
    "wechat_mp": {"enabled": True, "token": "test-token", "reply_timeout": 2},
}


def build_config(**overrides: Any) -> AppConfig:
    data = copy.deepcopy(BASE)
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(data.get(key), dict):
            data[key].update(value)
        else:
            data[key] = value
    return AppConfig.model_validate(data)


class FakeLLM:
    """记录调用参数的假模型。"""

    def __init__(self, reply: str = "好的呀", replies: list[str] | None = None) -> None:
        self.reply = reply
        self.replies = list(replies or [])
        self.calls: list[list[dict[str, str]]] = []
        self.last_usage: dict[str, int] = {"total_tokens": 20}

    def chat(self, messages: list[dict[str, str]]) -> str:  # noqa: D102
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else self.reply


@pytest.fixture
def config(tmp_path) -> AppConfig:
    return build_config(memory={"path": str(tmp_path / "bot.db")})


@pytest.fixture
def memory(tmp_path) -> ConversationMemory:
    m = ConversationMemory(tmp_path / "mem.db", ttl_minutes=0, max_turns=24)
    yield m
    m.close()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def handler(config, memory, llm) -> ChatHandler:
    return ChatHandler(config, llm, memory)
