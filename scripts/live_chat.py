"""命令行里直接和机器人对话：用真实配置、真实模型，不经过 QQ / 公众号。

适合快速调人格（改 config.yaml 的 system_prompt）和验证上下文记忆。

用法：
    python scripts/live_chat.py                 # 交互式，一行一句
    python scripts/live_chat.py --reset         # 先清空这个会话的记忆
    echo "你好" | python scripts/live_chat.py   # 管道输入（可连续多行）
    python scripts/live_chat.py --mock          # 用假模型，不花 token
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bot.config import AppConfig  # noqa: E402
from bot.handler import ChatHandler  # noqa: E402
from bot.llm import LLMClient  # noqa: E402
from bot.memory import ConversationMemory  # noqa: E402
from bot.models import Message  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="用真实模型在命令行里聊天")
    parser.add_argument("-c", "--config", default=None, help="配置文件路径")
    parser.add_argument("--session", default="cli", help="会话名，不同会话的记忆互相隔离")
    parser.add_argument("--reset", action="store_true", help="开始前清空该会话记忆")
    parser.add_argument("--mock", action="store_true", help="使用 mock 模型")
    parser.add_argument("--user", default="local", help="模拟的用户 ID（用来测试主人 / 客人身份）")
    parser.add_argument("--name", default="你", help="模拟的昵称")
    parser.add_argument("--image", help="附带一张本地图片，测试识图")
    args = parser.parse_args(argv)

    config = AppConfig.load(args.config)
    if args.mock:
        config.llm.provider = "mock"

    llm = LLMClient(config.llm)
    memory = ConversationMemory(
        config.memory.path,
        ttl_minutes=config.memory.ttl_minutes,
        max_turns=config.memory.max_turns,
    )

    def build(text: str) -> Message:
        return Message(
            platform="cli",
            chat_type="private",
            chat_id=args.session,
            user_id=args.user,
            user_name=args.name,
            text=text,
            images=[args.image] if args.image else [],
        )

    session_key = build("").session_key
    if args.reset:
        print(f"已清空会话 {session_key} 的 {memory.clear(session_key)} 条记忆")

    handler = ChatHandler(config, llm, memory)
    interactive = sys.stdin.isatty()
    if interactive:
        print(f"模型：{config.llm.provider} / {config.llm.model}    会话：{session_key}")
        print("直接输入问题，Ctrl+C 或输入 exit 结束。\n")

    def ask(text: str) -> None:
        started = time.time()
        out = handler.reply(build(text))
        elapsed = time.time() - started
        body = out.text if out else "（保持沉默）"
        print(f"{'AI' if interactive else ''} > {body or '（只发了附件）'}   [{elapsed:.1f}s]")
        if out and out.sticker:
            print(f"  [表情包] {Path(out.sticker).name}")
        if out and out.voice:
            print(f"  [语音] {Path(out.voice).name}")

    try:
        if not interactive:
            if args.image:
                text = " ".join(line.strip() for line in sys.stdin if line.strip())
                ask(text)
                return 0
            for line in sys.stdin:
                line = line.strip()
                if line:
                    ask(line)
            return 0

        while True:
            try:
                text = input("你 > ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if text.lower() in ("exit", "quit", "q"):
                break
            if text:
                ask(text)
    finally:
        memory.close()
        llm.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
