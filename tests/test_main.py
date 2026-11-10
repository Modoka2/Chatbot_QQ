from __future__ import annotations

from main import _init_files


def _make_templates(root):
    (root / ".env.example").write_text("DEEPSEEK_API_KEY=\n", encoding="utf-8")
    (root / "config.yaml").write_text("server:\n  port: 8080\n", encoding="utf-8")


def test_init_creates_local_files(tmp_path):
    _make_templates(tmp_path)
    assert _init_files(tmp_path) == 0
    assert (tmp_path / ".env").read_text(encoding="utf-8") == "DEEPSEEK_API_KEY=\n"
    assert (tmp_path / "config.local.yaml").read_text(encoding="utf-8").startswith("server:")


def test_init_does_not_overwrite_existing(tmp_path):
    _make_templates(tmp_path)
    (tmp_path / "config.local.yaml").write_text("keep me", encoding="utf-8")
    assert _init_files(tmp_path) == 0
    assert (tmp_path / "config.local.yaml").read_text(encoding="utf-8") == "keep me"


def test_init_fails_without_template(tmp_path):
    assert _init_files(tmp_path) == 1
