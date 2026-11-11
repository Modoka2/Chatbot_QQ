from __future__ import annotations

import base64
import json

import pytest

from bot.handler import _STICKER_MARKER
from bot.stickers import StickerBox

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def tagged_dir(tmp_path):
    d = tmp_path / "stickers"
    d.mkdir()
    (d / "happy.png").write_bytes(PNG)
    (d / "hungry.png").write_bytes(PNG)
    (d / "sad.png").write_bytes(PNG)
    (d / "tags.json").write_text(
        json.dumps(
            {"happy.png": ["开心", "得意"], "hungry.png": ["饿了"], "sad.png": ["委屈"]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return d


# ------------------------------------------------------------------ 标签
def test_box_loads_tags(tagged_dir):
    box = StickerBox(tagged_dir)
    assert set(box.all_tags()) == {"开心", "委屈", "得意", "饿了"}
    assert [p.name for p in box.by_tag("开心")] == ["happy.png"]


def test_box_unknown_tag_returns_empty(tagged_dir):
    box = StickerBox(tagged_dir)
    assert box.by_tag("晚安") == []
    assert box.random_for_tag("晚安") is None


def test_box_random_for_tag_returns_matching(tagged_dir):
    box = StickerBox(tagged_dir)
    picked = box.random_for_tag("饿了")
    assert picked is not None and picked.name == "hungry.png"


def test_box_broken_tags_file(tmp_path):
    d = tmp_path / "s"
    d.mkdir()
    (d / "a.png").write_bytes(PNG)
    (d / "tags.json").write_text("{不是 json", encoding="utf-8")
    box = StickerBox(d)
    assert box.count == 1
    assert box.all_tags() == []


# ------------------------------------------------------------------ 标记解析
def test_marker_regex_variants():
    for text in ("好呀[[表情:开心]]", "好呀【表情：开心】", "好呀[表情:开心]"):
        match = _STICKER_MARKER.search(text)
        assert match and match.group(1) == "开心"


def test_model_picks_sticker_by_tag(handler, tagged_dir):
    handler.config.sticker.dir = str(tagged_dir)
    handler._stickers = None
    handler.config.sticker.probability = 0
    handler.llm.reply = "好啦好啦，别难过了[[表情:委屈]]"

    out = handler.reply(_msg("今天好难过"))
    assert out is not None
    assert out.sticker.endswith("sad.png")
    assert "[[表情" not in out.text
    assert out.text == "好啦好啦，别难过了"


def test_unknown_tag_sends_no_sticker(handler, tagged_dir):
    handler.config.sticker.dir = str(tagged_dir)
    handler._stickers = None
    handler.llm.reply = "嗯嗯[[表情:睡觉]]"

    out = handler.reply(_msg("困了"))
    assert out.sticker == ""
    assert out.text == "嗯嗯"


def test_fuzzy_tag_match(handler, tagged_dir):
    handler.config.sticker.dir = str(tagged_dir)
    handler._stickers = None
    handler.llm.reply = "抱抱[[表情:有点委屈]]"

    out = handler.reply(_msg("难过"))
    assert out.sticker.endswith("sad.png")


def test_sticker_disabled_strips_marker(handler, tagged_dir):
    handler.config.sticker.dir = str(tagged_dir)
    handler.config.sticker.enabled = False
    handler.llm.reply = "好的[[表情:开心]]"

    out = handler.reply(_msg("早"))
    assert out.text == "好的"
    assert out.sticker == ""


def test_prompt_lists_tags(handler, tagged_dir):
    handler.config.sticker.dir = str(tagged_dir)
    handler._stickers = None
    handler.handle(_msg("你好"))
    system = handler.llm.calls[-1][0]["content"]
    assert "[表情包]" in system
    assert "[[表情:标签]]" in system
    assert "开心" in system


def _msg(text: str):
    from bot.models import Message

    return Message(
        platform="onebot11", chat_type="private", chat_id="10001",
        user_id="10001", user_name="小明", text=text,
    )
