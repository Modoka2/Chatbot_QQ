"""给 stickers/ 里的表情包自动打标签。

用视觉模型看每张图，从固定标签表里挑 1~3 个，写进 stickers/tags.json。
标签是模型选图的依据：回复里写 [[表情:饿了]] 就会挑一张带「饿了」标签的图。

用法：
    python scripts/tag_stickers.py               # 给所有没标签的图打标签
    python scripts/tag_stickers.py --force       # 全部重打
    python scripts/tag_stickers.py --list        # 只看现在打了什么
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bot.config import AppConfig  # noqa: E402
from bot.llm import LLMClient, LLMError  # noqa: E402
from bot.stickers import StickerBox  # noqa: E402
from bot.vision import ImageError, load_image, to_data_uri  # noqa: E402

TAGS = [
    "开心", "害羞", "得意", "无语", "生气", "委屈",
    "饿了", "摆烂", "收到", "疑问", "加油", "晚安",
]

PROMPT = (
    "这是一张聊天用的表情包。请从下面的标签里挑 1~3 个最贴切的：\n"
    + "、".join(TAGS)
    + "\n只输出标签本身，用顿号分隔，不要解释、不要加别的字。"
)


def tag_one(client: LLMClient, path: Path) -> list[str]:
    data, mime = load_image(path, max_mb=8)
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": PROMPT},
                {"type": "image_url", "image_url": {"url": to_data_uri(data, mime)}},
            ],
        }
    ]
    reply = client.chat(messages)
    return [t for t in TAGS if t in reply]


def main() -> int:
    parser = argparse.ArgumentParser(description="给表情包打标签")
    parser.add_argument("--force", action="store_true", help="已有标签也重打")
    parser.add_argument("--list", action="store_true", help="只列出当前标签")
    args = parser.parse_args()

    config = AppConfig.load()
    box = StickerBox(config.sticker.dir, config.sticker.max_mb)
    existing = box.tags()

    if args.list:
        for path in box.all():
            print(f"{path.name}: {'、'.join(existing.get(path.name, [])) or '（无）'}")
        return 0

    client = LLMClient(config.llm)
    tags = dict(existing)
    todo = [p for p in box.all() if args.force or p.name not in tags]
    if not todo:
        print("所有图片都已有标签，用 --force 重打。")
        return 0

    print(f"待打标签：{len(todo)} 张\n")
    for i, path in enumerate(todo, 1):
        try:
            picked = tag_one(client, path)
        except (ImageError, LLMError) as exc:
            print(f"  ✗ {path.name}: {exc}")
            continue
        if not picked:
            print(f"  ? {path.name}: 模型没给有效标签")
            continue
        tags[path.name] = picked
        print(f"  ✓ {path.name}: {'、'.join(picked)}")

    out = box.dir / "tags.json"
    out.write_text(json.dumps(tags, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n已写入 {out}（{len(tags)} 张有标签）")
    client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
