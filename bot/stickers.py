"""表情包库：扫描本地目录，随机挑一张发给用户。

设计上刻意保持简单：
- 只认本地文件，不联网下载（避免版权和失效链接）
- 只收常见图片格式，超过大小上限的忽略
- 随机不重复上一张，避免连着两次同一张
"""

from __future__ import annotations

import json
import logging
import random
from pathlib import Path

log = logging.getLogger(__name__)

EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


class StickerError(RuntimeError):
    pass


class StickerBox:
    def __init__(self, directory: str | Path, max_mb: float = 5.0) -> None:
        self.dir = Path(directory)
        self.limit = int(max_mb * 1024 * 1024)
        self.tags_path = self.dir / "tags.json"
        self._files: list[Path] = []
        self._last: Path | None = None
        self._tags: dict[str, list[str]] = {}
        self.reload()

    def reload(self) -> int:
        """重新扫描目录和标签，返回可用图片数量。"""
        if not self.dir.is_dir():
            log.warning("表情包目录不存在：%s", self.dir)
            self._files = []
            self._tags = {}
            return 0

        files: list[Path] = []
        for path in sorted(self.dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in EXTS:
                continue
            try:
                if path.stat().st_size > self.limit:
                    log.debug("表情包太大，跳过：%s", path.name)
                    continue
            except OSError:
                continue
            files.append(path)

        self._files = files
        self._tags = self._load_tags()
        if files:
            log.info("表情包库已加载：%d 张，%d 张有标签（%s）", len(files), len(self._tags), self.dir)
        else:
            log.warning("表情包目录里没有可用图片：%s", self.dir)
        return len(files)

    def _load_tags(self) -> dict[str, list[str]]:
        if not self.tags_path.is_file():
            return {}
        try:
            data = json.loads(self.tags_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            log.warning("标签文件读不出来：%s", exc)
            return {}
        tags: dict[str, list[str]] = {}
        for name, values in data.items():
            if isinstance(values, list):
                cleaned = [str(v).strip() for v in values if str(v).strip()]
                if cleaned:
                    tags[str(name)] = cleaned
        return tags

    # ---------------------------------------------------------------- 标签
    def tags(self) -> dict[str, list[str]]:
        return {name: list(values) for name, values in self._tags.items()}

    def all_tags(self) -> list[str]:
        seen: set[str] = set()
        for values in self._tags.values():
            seen.update(values)
        return sorted(seen)

    def by_tag(self, tag: str) -> list[Path]:
        """返回带这个标签的图片；没打标签时返回空。"""
        tag = (tag or "").strip()
        if not tag:
            return []
        return [p for p in self._files if tag in self._tags.get(p.name, [])]

    def random_for_tag(self, tag: str, rng: random.Random | None = None) -> Path | None:
        """按标签挑一张，尽量不重复上一张；标签没有匹配就返回 None。"""
        candidates = self.by_tag(tag)
        if not candidates:
            return None
        pool = [p for p in candidates if p != self._last] or candidates
        self._last = (rng or random).choice(pool)
        return self._last

    # ---------------------------------------------------------------- 随机
    @property
    def count(self) -> int:
        return len(self._files)

    def random(self, rng: random.Random | None = None) -> Path | None:
        """随机挑一张，尽量不和上一次重复。"""
        if not self._files:
            return None
        if len(self._files) == 1:
            self._last = self._files[0]
            return self._last
        if self._last is None:
            self._last = (rng or random).choice(self._files)
            return self._last

        pool = [p for p in self._files if p != self._last]
        self._last = (rng or random).choice(pool)
        return self._last

    def all(self) -> list[Path]:
        return list(self._files)
