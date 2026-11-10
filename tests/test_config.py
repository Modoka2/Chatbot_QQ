from __future__ import annotations

from bot.config import AppConfig
from tests.conftest import build_config


def test_defaults_are_sane():
    cfg = AppConfig.model_validate({})
    assert cfg.server.port == 8080
    assert cfg.llm.provider == "openai"
    assert cfg.reply.group_require_at is True
    assert cfg.memory.max_turns >= 2


def test_unknown_keys_are_ignored():
    cfg = build_config(something_new={"a": 1})
    assert cfg.server.port == 8080


def test_env_expansion(monkeypatch, tmp_path):
    monkeypatch.setenv("BOT_TEST_KEY", "sk-123")
    path = tmp_path / "c.yaml"
    path.write_text(
        "llm:\n  api_key: ${BOT_TEST_KEY}\n  model: ${NOT_EXISTS}\n",
        encoding="utf-8",
    )
    cfg = AppConfig.load(path)
    assert cfg.llm.api_key == "sk-123"
    assert cfg.llm.model == ""  # 缺失变量替换为空串


def test_load_missing_explicit_path_raises(tmp_path):
    import pytest

    with pytest.raises(FileNotFoundError):
        AppConfig.load(tmp_path / "nope.yaml")


def test_local_config_takes_precedence(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text("server:\n  port: 1111\n", encoding="utf-8")
    (tmp_path / "config.local.yaml").write_text("server:\n  port: 2222\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert AppConfig.load().server.port == 2222
