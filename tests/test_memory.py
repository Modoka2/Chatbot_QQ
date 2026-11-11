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
    assert memory.stats() == {"sessions": 1, "turns": 2, "summaries": 0}
    assert memory.clear("s") == 2
    assert memory.load("s") == []
    assert memory.stats() == {"sessions": 0, "turns": 0, "summaries": 0}


# ------------------------------------------------------------------ 摘要
def test_append_keeps_all_rows(memory):
    memory.max_turns = 4
    for i in range(6):
        memory.append("s", "user", f"消息{i}")
    assert memory.count("s") == 6
    assert len(memory.load("s")) == 4  # 读的时候仍然只给最近 4 条


def test_compact_summarizes_oldest(memory):
    memory.max_turns = 4
    for i in range(6):
        memory.append("s", "user", f"消息{i}")

    calls = []

    def fake(previous, chunk):
        calls.append((previous, chunk))
        return "用户和机器人聊过消息0到1"

    removed = memory.compact("s", keep=4, summarize=fake, chunk=8)
    assert removed == 2
    assert memory.count("s") == 4
    assert memory.get_summary("s") == "用户和机器人聊过消息0到1"
    assert calls[0][0] == ""
    assert "消息0" in calls[0][1]


def test_compact_merges_previous_summary(memory):
    for i in range(4):
        memory.append("s", "user", f"旧{i}")

    memory.compact("s", keep=0, summarize=lambda p, c: "第一版摘要", chunk=8)

    for i in range(3):
        memory.append("s", "user", f"新{i}")

    seen = {}

    def fake(previous, chunk):
        seen["previous"] = previous
        return "第二版摘要"

    memory.compact("s", keep=0, summarize=fake, chunk=8)
    assert seen["previous"] == "第一版摘要"
    assert memory.get_summary("s") == "第二版摘要"


def test_compact_keeps_previous_on_failure(memory):
    for i in range(4):
        memory.append("s", "user", f"旧{i}")
    memory.compact("s", keep=0, summarize=lambda p, c: "原有摘要", chunk=8)

    for i in range(2):
        memory.append("s", "user", f"新{i}")

    def boom(previous, chunk):
        raise RuntimeError("模型挂了")

    removed = memory.compact("s", keep=0, summarize=boom, chunk=8)
    assert removed == 2  # 仍然裁剪，避免无限增长
    assert memory.get_summary("s") == "原有摘要"


def test_clear_removes_summary(memory):
    memory.append("s", "user", "内容")
    memory.compact("s", keep=0, summarize=lambda p, c: "摘要", chunk=8)
    assert memory.get_summary("s") == "摘要"
    memory.clear("s")
    assert memory.get_summary("s") == ""


def test_persists_across_reopen(tmp_path):
    path = tmp_path / "p.db"
    first = ConversationMemory(path, ttl_minutes=0, max_turns=10)
    first.append_turn("s", "重启前", "记住了")
    first.close()

    second = ConversationMemory(path, ttl_minutes=0, max_turns=10)
    assert [h["content"] for h in second.load("s")] == ["重启前", "记住了"]
    second.close()
