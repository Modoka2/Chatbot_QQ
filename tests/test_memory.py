from __future__ import annotations

from bot.memory import ConversationMemory


def test_append_and_load_in_order(memory):
    memory.append_turn("s1", "你好", "你好呀")
    memory.append_turn("s1", "再见", "拜拜")
    history = memory.load("s1")
    assert [h["role"] for h in history] == ["user", "assistant", "user", "assistant"]
    assert history[0]["content"] == "你好"
    assert history[-1]["content"] == "拜拜"


def test_sessions_are_isolated(memory):
    memory.append("qq:group:1", "user", "群里的消息")
    memory.append("qq:private:2", "user", "私聊的消息")
    assert [h["content"] for h in memory.load("qq:group:1")] == ["群里的消息"]
    assert [h["content"] for h in memory.load("qq:private:2")] == ["私聊的消息"]


def test_max_turns_keeps_only_latest(memory):
    memory.max_turns = 4
    for i in range(6):
        memory.append("s", "user", f"消息{i}")
    contents = [h["content"] for h in memory.load("s")]
    assert contents == ["消息2", "消息3", "消息4", "消息5"]


def test_ttl_expires_history(tmp_path):
    now = [1000.0]
    mem = ConversationMemory(tmp_path / "t.db", ttl_minutes=1, max_turns=10, clock=lambda: now[0])
    mem.append("s", "user", "旧消息")
    assert mem.load("s")
    now[0] += 61
    assert mem.load("s") == []
    assert mem.stats()["turns"] == 1  # 过期只是不参与上下文，prune 才物理删除
    assert mem.prune() == 1
    assert mem.stats()["turns"] == 0
    mem.close()


def test_clear_and_stats(memory):
    memory.append_turn("s", "a", "b")
    assert memory.stats() == {"sessions": 1, "turns": 2}
    assert memory.clear("s") == 2
    assert memory.load("s") == []
    assert memory.stats() == {"sessions": 0, "turns": 0}


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "p.db"
    first = ConversationMemory(path, ttl_minutes=0, max_turns=10)
    first.append_turn("s", "重启前", "记住了")
    first.close()

    second = ConversationMemory(path, ttl_minutes=0, max_turns=10)
    assert [h["content"] for h in second.load("s")] == ["重启前", "记住了"]
    second.close()
