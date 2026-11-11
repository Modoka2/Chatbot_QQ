from __future__ import annotations

import pytest

from bot.affinity import AffinityStore
from bot.config import AffinityConfig
from bot.models import Message


@pytest.fixture
def store(tmp_path):
    s = AffinityStore(tmp_path / "aff.db", AffinityConfig())
    yield s
    s.close()


@pytest.fixture
def clock_store(tmp_path):
    now = [1_000_000.0]
    s = AffinityStore(tmp_path / "aff2.db", AffinityConfig(), clock=lambda: now[0])
    s._now = now
    yield s
    s.close()


def test_initial_values(store):
    assert store.get("qq:stranger").score == 50
    assert store.get("qq:owner", is_owner=True).score == 80


def test_owner_never_drops_below_80(store):
    for _ in range(20):
        store.touch("qq:owner", "废物，滚，讨厌你", is_owner=True)
    rel = store.get("qq:owner", is_owner=True)
    assert rel.score == 80
    assert rel.mood < 0


def test_guest_never_exceeds_80(store):
    for _ in range(30):
        store.touch("qq:guest", "谢谢你，你最可爱了，真贴心")
    assert store.get("qq:guest").score == 80


def test_positive_raises_score_and_mood(store):
    store.touch("qq:100", "谢谢你，你真可爱")
    rel = store.get("qq:100")
    assert rel.score > 50
    assert rel.mood > 0
    assert rel.interactions == 1
    assert rel.last_reason == "被夸了"


def test_negative_lowers_score(store):
    store.touch("qq:100", "你真笨，滚")
    rel = store.get("qq:100")
    assert rel.score < 50
    assert rel.mood < 0
    assert rel.last_reason == "被骂了"


def test_rushed_counts_as_negative(store):
    store.touch("qq:100", "快点啊，慢死了")
    assert store.get("qq:100").score < 50


def test_score_is_clamped(store):
    for _ in range(60):
        store.touch("qq:100", "谢谢，你最棒了，真可爱")
    assert store.get("qq:100").score <= 80  # 非主人封顶 80

    for _ in range(80):
        store.touch("qq:200", "你这个废物，滚，讨厌")
    assert store.get("qq:200").score >= 0


def test_decay_towards_neutral(clock_store):
    clock_store.touch("qq:100", "谢谢，你真可爱")
    high = clock_store.get("qq:100").score
    assert high > 50

    clock_store._now[0] += 10 * 86400  # 10 天后
    later = clock_store.get("qq:100").score
    assert 50 <= later < high


def test_mood_halves_after_half_life(clock_store):
    clock_store.touch("qq:100", "谢谢，你真可爱")
    mood = clock_store.get("qq:100").mood
    assert mood > 0

    clock_store._now[0] += 3 * 3600
    assert clock_store.get("qq:100").mood == pytest.approx(mood / 2, rel=0.05)


def test_describe_has_relation_and_mood(store):
    store.touch("qq:100", "谢谢你")
    text = store.describe("qq:100")
    assert text.startswith("[关系]")
    assert "好感度" in text
    assert "心情" in text


def test_low_score_adds_cold_hint(store):
    for _ in range(10):
        store.touch("qq:100", "废物，滚")
    assert "不太想搭理" in store.describe("qq:100")


def test_high_score_adds_warm_hint(store):
    for _ in range(20):
        store.touch("qq:100", "谢谢，你真可爱")
    assert "挺喜欢" in store.describe("qq:100", is_owner=True)


def test_report_format(store):
    store.touch("qq:100", "你好")
    report = store.report("qq:100")
    assert "好感度" in report
    assert "聊过 1 次" in report


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "persist.db"
    first = AffinityStore(path, AffinityConfig())
    first.touch("qq:100", "谢谢")
    first.close()

    second = AffinityStore(path, AffinityConfig())
    assert second.get("qq:100").interactions == 1
    second.close()


# ------------------------------------------------------------------ 接入聊天
def _msg(text: str, user_id: str = "20002") -> Message:
    return Message(
        platform="onebot11", chat_type="private", chat_id=user_id,
        user_id=user_id, user_name="路人", text=text,
    )


def test_system_prompt_has_relation(handler):
    handler.config.affinity.enabled = True
    handler.handle(_msg("你好"))
    assert "[关系]" in handler.llm.calls[-1][0]["content"]


def test_affinity_command(handler):
    handler.config.affinity.enabled = True
    handler.handle(_msg("谢谢你，真可爱"))
    reply = handler.handle(_msg("/好感"))
    assert "好感度" in reply


def test_negative_message_lowers_score(handler):
    handler.config.affinity.enabled = True
    before = handler.affinity.get("onebot11:20002").score
    handler.handle(_msg("你真笨，滚"))
    after = handler.affinity.get("onebot11:20002").score
    assert after < before
