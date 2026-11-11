"""配置加载。

优先级：显式传入的路径 > config.local.yaml > config.yaml > 内置默认值。
字符串里的 ``${ENV_NAME}`` 会被环境变量替换，方便把密钥放进 .env 而不是仓库里。
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field

log = logging.getLogger(__name__)

_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

DEFAULT_SYSTEM_PROMPT = (
    "你是一个正在 QQ / 微信 上和朋友聊天的中文 AI 助手。"
    "说话自然、口语化、简短，一次回复尽量不超过 150 字。"
    "不要使用 Markdown 标题、表格和代码块，因为聊天窗口不支持渲染。"
    "不确定的事情要直说，不要编造事实。"
)


def _expand_env(value: Any) -> Any:
    """递归替换 ${VAR}；变量缺失时替换为空串。"""
    if isinstance(value, str):
        return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), ""), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def load_dotenv(path: str | Path = ".env") -> None:
    """极简 .env 读取：已存在的环境变量优先，不会覆盖。"""
    p = Path(path)
    if not p.is_file():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


class _Model(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ServerConfig(_Model):
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"


class LLMConfig(_Model):
    provider: str = "openai"  # openai（任意 OpenAI 兼容接口）| mock
    base_url: str = "https://api.deepseek.com/v1"
    api_key: str = ""
    model: str = "deepseek-chat"
    temperature: float = 0.8
    max_tokens: int = 800
    timeout: float = 30.0
    max_retries: int = 1
    system_prompt: str = DEFAULT_SYSTEM_PROMPT
    history_window: int = 16  # 带给模型的历史消息条数（含双方）
    daily_token_budget: int = 300_000
    """每天最多消耗多少 token，0 = 不限制。超了直接拒答，防止刷爆钱包。"""
    token_budget_notice: str = "今天的额度用完啦，明天再来找我吧～"


class RateLimitConfig(_Model):
    per_user_per_minute: int = 8
    per_user_per_day: int = 200
    per_chat_per_minute: int = 40
    per_chat_per_day: int = 600
    per_day_total: int = 2000
    """全局每天最多回复多少条，0 = 不限制。"""
    cooldown_seconds: float = 0.0
    """同一用户两条消息的最小间隔，用来吞掉连点造成的重复；0 = 关闭。"""
    notice_cooldown_minutes: float = 3.0
    """被限流时多久最多提醒一次，避免「限流提示」本身刷屏。"""
    notice: str = "慢一点啦，我等下再回答你～"


class ReplyConfig(_Model):
    private_enabled: bool = True
    group_enabled: bool = True
    group_require_at: bool = True
    """群聊里必须 @机器人 才回复；设为 false 则回复所有消息（不建议）。"""
    group_prefixes: list[str] = Field(default_factory=list)
    """群聊里也可以用前缀触发，例如 ["/ai", "#ai"]。"""
    command_prefix: str = "/"
    max_reply_chars: int = 900
    max_input_chars: int = 500
    """单条消息超过这个长度就截断，防止有人贴一整本书进来烧 token。"""
    unsupported_hint: str = "我暂时只看得懂文字，图片/语音可以配一句说明哦。"
    image_disabled_hint: str = "我现在的识图还没开，配一句话说说你想让我看什么吧～"
    rate_limit: RateLimitConfig = Field(default_factory=RateLimitConfig)


class WhitelistConfig(_Model):
    user_ids: list[str] = Field(default_factory=list)
    """为空表示不限制用户。QQ 号为数字字符串，公众号为 OpenID。"""
    group_ids: list[str] = Field(default_factory=list)
    """为空表示不限制群。"""
    admin_ids: list[str] = Field(default_factory=list)
    """能用 /status、/pause、/resume 的人；为空表示谁都不能用。"""
    reject_notice: str = ""
    """被白名单拦下时回一句什么；留空 = 完全静默（推荐，别给探路的人反馈）。"""


class SearchConfig(_Model):
    enabled: bool = True
    provider: str = "auto"
    """auto = 360 搜索 → 搜狗；也可 so / sogou / searxng / tavily。"""
    max_results: int = 5
    timeout: float = 12.0
    max_snippet: int = 200
    auto_keywords: list[str] = Field(
        default_factory=lambda: [
            "搜一下", "搜搜", "搜索", "查一下", "查查", "百度一下", "最新消息", "新闻", "实时",
        ]
    )
    """消息里出现这些词，就先去搜一圈再回答。"""
    searxng_url: str = ""
    tavily_key: str = ""


class StickerConfig(_Model):
    enabled: bool = True
    dir: str = "stickers"
    """表情包目录（相对项目根目录）。把 png/jpg/gif 丢进去即可。"""
    probability: float = 0.15
    """普通回复后附带表情包的概率，0~1。"""
    trigger_keywords: list[str] = Field(
        default_factory=lambda: ["表情包", "来个表情", "发张图", "斗图", "发表情"]
    )
    """命中这些词就直接发表情包，不调模型。"""
    trigger_text: str = "呐，给你～"
    group_enabled: bool = True
    private_enabled: bool = True
    max_mb: float = 5.0


class VisionConfig(_Model):
    enabled: bool = True
    model: str = "deepseek-chat"
    """视觉模型；deepseek-chat 实测可以直接读图，不必另开服务商。"""
    base_url: str = ""
    """留空则复用 llm.base_url。"""
    api_key: str = ""
    """留空则复用 llm.api_key。"""
    timeout: float = 60.0
    max_images: int = 3
    max_mb: float = 8.0
    prompt: str = (
        "用户发来一张图，用中文说清楚图里有什么；"
        "如果是截图、题目或表格，把关键内容读出来；不要臆测看不清的细节。"
    )


class OwnerConfig(_Model):
    ids: list[str] = Field(default_factory=list)
    """谁是「主人」。填 QQ 号 / 公众号 OpenID；为空时退回 admin_ids。"""
    title: str = "主人"
    """对主人的称呼。"""
    guest_title: str = "客人"
    """对其他人（非主人）的称呼。"""


class SummaryConfig(_Model):
    enabled: bool = True
    model: str = ""
    """压缩用的模型；留空复用 llm.model。"""
    every_turns: int = 8
    """一次最多压缩多少条旧消息。"""
    max_chars: int = 160
    """压缩后的「前情提要」最多多少字。"""


class MemoryConfig(_Model):
    path: str = "data/bot.db"
    ttl_minutes: int = 180
    """会话闲置多久后自动遗忘，0 表示永不过期。"""
    max_turns: int = 24
    """单会话保留的原始消息条数上限，超出的会先被压缩成摘要。"""
    summary: SummaryConfig = Field(default_factory=SummaryConfig)


class OneBot11Config(_Model):
    enabled: bool = True
    api_base: str = "http://127.0.0.1:3000"
    """NapCat / LLOneBot 的 HTTP API 地址。"""
    access_token: str = ""
    secret: str = ""
    """HTTP 上报签名密钥，留空则不校验。"""
    self_ids: list[str] = Field(default_factory=list)
    """机器人自己的 QQ 号，用于判断是否被 @；留空则依赖事件里的 self_id。"""
    timeout: float = 15.0
    async_reply: bool = True
    """立即返回 200，回复在后台发送，避免 QQ 端因超时重试。"""


class WeChatMPConfig(_Model):
    enabled: bool = True
    token: str = ""
    app_id: str = ""
    app_secret: str = ""
    """填写后才能使用「客服消息」在超时后补发答案。"""
    reply_timeout: float = 4.5
    """公众号被动回复必须 5 秒内返回，留一点余量。"""
    timeout_text: str = "这个问题有点难，我再想想，你稍后再问一次～"
    path: str = "/wechat_mp"


class AppConfig(_Model):
    server: ServerConfig = Field(default_factory=ServerConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    memory: MemoryConfig = Field(default_factory=MemoryConfig)
    reply: ReplyConfig = Field(default_factory=ReplyConfig)
    whitelist: WhitelistConfig = Field(default_factory=WhitelistConfig)
    owner: OwnerConfig = Field(default_factory=OwnerConfig)
    vision: VisionConfig = Field(default_factory=VisionConfig)
    sticker: StickerConfig = Field(default_factory=StickerConfig)
    search: SearchConfig = Field(default_factory=SearchConfig)
    onebot11: OneBot11Config = Field(default_factory=OneBot11Config)
    wechat_mp: WeChatMPConfig = Field(default_factory=WeChatMPConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "AppConfig":
        load_dotenv()
        if path is not None:
            candidates = [Path(path)]
        else:
            candidates = [Path("config.local.yaml"), Path("config.yaml")]

        data: dict[str, Any] = {}
        for candidate in candidates:
            if candidate.is_file():
                raw = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                if not isinstance(raw, dict):
                    raise ValueError(f"配置文件 {candidate} 顶层必须是映射（dict）")
                data = raw
                log.info("已加载配置：%s", candidate.resolve())
                break
        else:
            if path is not None:
                raise FileNotFoundError(f"找不到配置文件：{path}")
            log.warning("未找到 config.yaml，全部使用默认配置")

        return cls.model_validate(_expand_env(data))
