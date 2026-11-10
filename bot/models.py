"""平台无关的统一消息模型。

所有适配器都把各自的协议转换成 :class:`Message`，处理核心只认这个模型，
新增平台（企微、飞书、Telegram……）时不需要改动业务逻辑。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class Message:
    """一条进入机器人的用户消息。"""

    platform: str
    """来源平台：onebot11 / wechat_mp。"""

    chat_type: str
    """聊天类型：private（私聊）或 group（群聊）。"""

    chat_id: str
    """会话 ID：私聊为对方 ID，群聊为群号。"""

    user_id: str
    """发送者 ID：QQ 号或公众号 OpenID。"""

    user_name: str = ""
    """发送者昵称，用于群聊里给模型区分说话人。"""

    text: str = ""
    """已清洗的文本内容，非文本消息以 [图片] 等占位符表示。"""

    at_me: bool = False
    """本轮消息是否 @ 了机器人。"""

    message_id: str = ""
    """平台消息 ID，便于去重与日志排查。"""

    images: list[str] = field(default_factory=list)
    """图片地址（http(s) / file:// / 本地路径），按消息里的顺序。"""

    raw: dict[str, Any] = field(default_factory=dict, repr=False)
    """原始事件，排障时用得到。"""

    @property
    def session_key(self) -> str:
        """记忆隔离键：不同平台、不同会话互不串台。"""
        return f"{self.platform}:{self.chat_type}:{self.chat_id}"

    @property
    def display_name(self) -> str:
        return self.user_name or self.user_id


@dataclass(slots=True)
class Reply:
    """机器人要发出去的东西：一段文字，可选再带一张表情包。"""

    text: str = ""
    sticker: str = ""
    """表情包本地路径；空表示不发图。"""

    def is_empty(self) -> bool:
        return not self.text and not self.sticker
