"""好感度与心情：给「活人感」加一层能累积、会变化的关系状态。

规则全部可解释，不做玄学：
- **好感度** 0~100，初始 50（主人 70）；被夸/道谢涨、被骂/催促掉，长期不聊会慢慢回落到 50
- **心情** -1~1，短期波动，按半衰期衰减回平静；深夜、被催会往下掉
- 每轮把「聊过几次 + 好感度 + 当前心情」注入 system prompt，由模型自己决定语气

设计取舍：
- 不额外调用模型做情感分析（省钱、可测），只用关键词规则
- 掉分比涨分快（负面权重 2 倍），符合真实的人际感受
- 存独立的 affinity 表，和聊天记忆分开，`/reset` 不会冲掉关系
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .config import AffinityConfig

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS affinity (
    user_key     TEXT PRIMARY KEY,
    score        REAL NOT NULL,
    mood         REAL NOT NULL DEFAULT 0,
    interactions INTEGER NOT NULL DEFAULT 0,
    last_reason  TEXT NOT NULL DEFAULT '',
    updated_at   REAL NOT NULL
);
"""

_POSITIVE = (
    "谢谢", "感谢", "喜欢", "爱你", "可爱", "好棒", "厉害", "辛苦", "乖",
    "真好", "贴心", "温柔", "抱住", "抱抱", "夸", "崇拜",
)
_NEGATIVE = (
    "笨", "蠢", "垃圾", "滚", "闭嘴", "恶心", "讨厌", "傻", "废物",
    "烦死", "神经", "没用", "垃圾东西", "去死",
)
_RUSHED = ("快点", "赶紧", "别废话", "磨叽", "慢死", "催")


@dataclass(slots=True)
class Relation:
    score: float = 50.0
    mood: float = 0.0
    interactions: int = 0
    last_reason: str = ""

    @property
    def score_label(self) -> str:
        if self.score < 25:
            return "冷淡"
        if self.score < 45:
            return "生分"
        if self.score < 65:
            return "还行"
        if self.score < 85:
            return "熟人"
        return "很熟"

    @property
    def mood_label(self) -> str:
        if self.mood < -0.5:
            return "一肚子气"
        if self.mood < -0.15:
            return "有点闷"
        if self.mood < 0.15:
            return "平静"
        if self.mood < 0.5:
            return "心情不错"
        return "挺开心"


class AffinityStore:
    def __init__(
        self,
        path: str | Path = "data/bot.db",
        config: AffinityConfig | None = None,
        clock=time.time,
    ) -> None:
        self.config = config or AffinityConfig()
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    # ---------------------------------------------------------------- 读取
    def _row(self, user_key: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM affinity WHERE user_key = ?", (user_key,)
            ).fetchone()

    def get(self, user_key: str, is_owner: bool = False) -> Relation:
        """读取关系状态，并顺手做时间衰减。"""
        row = self._row(user_key)
        now = self._clock()
        if row is None:
            relation = Relation(
                score=self.config.owner_initial if is_owner else self.config.initial,
                mood=0.0,
                interactions=0,
            )
            self._save(user_key, relation, now)
            return relation

        relation = Relation(
            score=float(row["score"]),
            mood=float(row["mood"]),
            interactions=int(row["interactions"]),
            last_reason=str(row["last_reason"]),
        )
        hours = max(0.0, (now - float(row["updated_at"])) / 3600)
        if hours > 0:
            # 好感度慢慢回落到 50
            drift = self.config.daily_decay * (hours / 24)
            if relation.score > 50:
                relation.score = max(50.0, relation.score - drift)
            elif relation.score < 50:
                relation.score = min(50.0, relation.score + drift)
            # 心情按半衰期衰减
            relation.mood *= 0.5 ** (hours / max(0.1, self.config.mood_half_life_hours))
            if abs(relation.mood) < 0.01:
                relation.mood = 0.0
        return relation

    # ---------------------------------------------------------------- 写入
    def _save(self, user_key: str, relation: Relation, now: float) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO affinity(user_key, score, mood, interactions, last_reason, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_key) DO UPDATE SET score = excluded.score, mood = excluded.mood, "
                "interactions = excluded.interactions, last_reason = excluded.last_reason, "
                "updated_at = excluded.updated_at",
                (
                    user_key,
                    relation.score,
                    relation.mood,
                    relation.interactions,
                    relation.last_reason,
                    now,
                ),
            )

    def detect(self, text: str) -> tuple[float, float, str]:
        """返回 (好感度变化, 心情变化, 原因)。纯规则，可解释。"""
        text = text or ""
        positive = sum(1 for w in _POSITIVE if w in text)
        negative = sum(1 for w in _NEGATIVE if w in text)
        rushed = sum(1 for w in _RUSHED if w in text)

        delta = min(positive, 3) * self.config.positive_step
        delta -= min(negative, 2) * self.config.negative_step
        if rushed and not positive:
            delta -= 1.0

        mood = min(positive, 3) * 0.18
        mood -= min(negative, 2) * 0.30
        if rushed and not positive:
            mood -= 0.12

        reason = ""
        if negative:
            reason = "被骂了"
        elif rushed:
            reason = "被催了"
        elif positive:
            reason = "被夸了"
        return delta, mood, reason

    def touch(self, user_key: str, text: str, is_owner: bool = False) -> Relation:
        """记录一次互动，更新好感度和心情。"""
        relation = self.get(user_key, is_owner)
        delta, mood_delta, reason = self.detect(text)

        relation.score = min(100.0, max(0.0, relation.score + self.config.per_message + delta))
        relation.mood = min(1.0, max(-1.0, relation.mood + mood_delta))
        relation.interactions += 1
        if reason:
            relation.last_reason = reason
        self._save(user_key, relation, self._clock())
        if reason:
            log.info("关系更新 %s：%+0.1f -> %.0f（%s）", user_key, delta, relation.score, reason)
        return relation

    # ---------------------------------------------------------------- 展示
    def describe(self, user_key: str, is_owner: bool = False) -> str:
        """给 system prompt 的关系描述。"""
        if not self.config.enabled:
            return ""
        relation = self.get(user_key, is_owner)
        parts = [
            f"[关系] 你和对方聊过 {relation.interactions} 次，"
            f"好感度 {relation.score:.0f}/100（{relation.score_label}），"
            f"你现在的心情：{relation.mood_label}。"
        ]
        if relation.score < 30:
            parts.append("你不太想搭理对方，可以回得更短、更敷衍。")
        elif relation.score < 45:
            parts.append("你保持礼貌，但不想太热络。")
        elif relation.score >= 80:
            parts.append("你挺喜欢跟对方聊，可以主动关心、开点小玩笑。")
        if relation.mood < -0.3:
            parts.append("你心情不好，语气会冲一点，别端着。")
        elif relation.mood > 0.5:
            parts.append("你心情很好，说话会轻快些。")
        return "".join(parts)

    def report(self, user_key: str, is_owner: bool = False) -> str:
        relation = self.get(user_key, is_owner)
        return (
            f"好感度 {relation.score:.0f}/100（{relation.score_label}）\n"
            f"心情：{relation.mood_label}\n"
            f"聊过 {relation.interactions} 次"
            + (f"\n最近：{relation.last_reason}" if relation.last_reason else "")
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
