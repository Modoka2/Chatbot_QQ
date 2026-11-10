"""表情包库：扫描本地目录，随机挑一张发给用户。

设计上刻意保持简单：
- 只认本地文件，不联网下载（避免版权和失效链接）
- 只收常见图片格式，超过大小上限的忽略
- 随机不重复上一张，避免连着两次同一张
"""

from __future__ import annotations

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
        self._files: list[Path] = []
        self._last: Path | None = None
        self.reload()

    def reload(self) -> int:
        """重新扫描目录，返回可用图片数量。"""
        if not self.dir.is_dir():
            log.warning("表情包目录不存在：%s", self.dir)
            self._files = []
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
        if files:
            log.info("表情包库已加载：%d 张（%s）", len(files), self.dir)
        else:
            log.warning("表情包目录里没有可用图片：%s", self.dir)
        return len(files)

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
