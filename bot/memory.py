"""会话记忆：SQLite 持久化 + 滑动窗口 + 闲置过期。

设计取舍：
- 用 SQLite 而不是内存字典，重启后上下文不丢；
- 每个「平台 + 会话」一个 key，私聊和群聊互不串台；
- 只保留最近 max_turns 条，防止 token 越滚越大；
- 超过 ttl_minutes 没说话就自动遗忘（prune 时物理删除）。
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS turns (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT    NOT NULL,
    role        TEXT    NOT NULL,
    content     TEXT    NOT NULL,
    created_at  REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_turns_session ON turns(session_key, id);
CREATE INDEX IF NOT EXISTS idx_turns_created ON turns(created_at);
"""


class ConversationMemory:
    def __init__(
        self,
        path: str | Path = "data/bot.db",
        ttl_minutes: int = 180,
        max_turns: int = 24,
        clock=time.time,
    ) -> None:
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(0, int(ttl_minutes)) * 60
        self.max_turns = max(2, int(max_turns))
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------------- 读写
    def load(self, session_key: str) -> list[dict[str, str]]:
        """按时间顺序返回该会话最近的历史消息。"""
        since = self._clock() - self.ttl_seconds if self.ttl_seconds else 0.0
        with self._lock:
            rows = self._conn.execute(
                "SELECT role, content FROM turns "
                "WHERE session_key = ? AND created_at >= ? "
                "ORDER BY id DESC LIMIT ?",
                (session_key, since, self.max_turns),
            ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]

    def append(self, session_key: str, role: str, content: str) -> None:
        """写入一条消息，并把该会话裁剪到 max_turns 条以内。"""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO turns(session_key, role, content, created_at) VALUES (?, ?, ?, ?)",
                (session_key, role, content, self._clock()),
            )
            self._conn.execute(
                "DELETE FROM turns WHERE session_key = ? AND id NOT IN ("
                "  SELECT id FROM turns WHERE session_key = ? ORDER BY id DESC LIMIT ?"
                ")",
                (session_key, session_key, self.max_turns),
            )

    def append_turn(self, session_key: str, user_text: str, assistant_text: str) -> None:
        self.append(session_key, "user", user_text)
        self.append(session_key, "assistant", assistant_text)

    def clear(self, session_key: str) -> int:
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM turns WHERE session_key = ?", (session_key,))
            return cur.rowcount

    def last_active(self, session_key: str) -> float | None:
        """这个会话最后一次说话的时间（epoch 秒），没有记录返回 None。"""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(created_at) FROM turns WHERE session_key = ?", (session_key,)
            ).fetchone()
        return float(row[0]) if row and row[0] else None

    # ---------------------------------------------------------------- 维护
    def prune(self) -> int:
        """删除所有过期会话，返回删除条数。"""
        if not self.ttl_seconds:
            return 0
        cutoff = self._clock() - self.ttl_seconds
        with self._lock, self._conn:
            cur = self._conn.execute("DELETE FROM turns WHERE created_at < ?", (cutoff,))
            return cur.rowcount

    def stats(self) -> dict[str, int]:
        with self._lock:
            sessions = self._conn.execute("SELECT COUNT(DISTINCT session_key) FROM turns").fetchone()[0]
            turns = self._conn.execute("SELECT COUNT(*) FROM turns").fetchone()[0]
        return {"sessions": int(sessions), "turns": int(turns)}

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "ConversationMemory":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
