"""大模型调用层。

统一走 OpenAI 兼容的 ``/chat/completions`` 协议，因此 DeepSeek、Kimi、通义、
硅基流动、Ollama、OneAPI / NewAPI 之类的网关都可以直接用，改 base_url + model 即可。
额外提供 ``mock`` 提供商，方便没有 API Key 时把整条链路跑通。
"""

from __future__ import annotations

import logging
import time
from typing import Any

import requests

from .config import LLMConfig

log = logging.getLogger(__name__)


class LLMError(RuntimeError):
    """模型调用失败（网络 / 鉴权 / 解析 / 上游错误）。"""


class LLMClient:
    def __init__(self, config: LLMConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self._session = session or requests.Session()
        self._endpoint = self._build_endpoint(config.base_url)
        self.last_usage: dict[str, int] = {}
        """最近一次调用的 token 用量，给每日预算统计用。"""

    @staticmethod
    def _build_endpoint(base_url: str) -> str:
        base = (base_url or "").rstrip("/")
        if base.endswith("/chat/completions"):
            return base
        return f"{base}/chat/completions"

    def chat(self, messages: list[dict[str, Any]]) -> str:
        """发送一轮对话，返回助手文本；失败抛 :class:`LLMError`。

        ``content`` 可以是字符串，也可以是 OpenAI 多模态的消息段数组。
        """
        if self.config.provider == "mock":
            return self._mock(messages)
        return self._request(messages)

    # ---------------------------------------------------------------- 内部
    def _mock(self, messages: list[dict[str, Any]]) -> str:
        last = next(
            (m["content"] for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        self.last_usage = {"total_tokens": len(last) + len(messages) * 4}
        return f"【mock 模式】收到：{last}（配置好 API Key 后就是真实模型回答）"

    def _request(self, messages: list[dict[str, Any]]) -> str:
        cfg = self.config
        if not cfg.api_key:
            raise LLMError("未配置 llm.api_key，请填写 DeepSeek / OpenAI 等平台的密钥")

        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {cfg.api_key}"
        payload = {
            "model": cfg.model,
            "messages": messages,
            "temperature": cfg.temperature,
            "max_tokens": cfg.max_tokens,
            "stream": False,
        }

        last_error = "未知错误"
        for attempt in range(cfg.max_retries + 1):
            started = time.monotonic()
            try:
                resp = self._session.post(
                    self._endpoint,
                    json=payload,
                    headers=headers,
                    timeout=(10, cfg.timeout),
                )
            except requests.RequestException as exc:
                last_error = f"网络错误：{exc}"
                log.warning("LLM 请求失败（第 %d 次）：%s", attempt + 1, exc)
            else:
                elapsed = time.monotonic() - started
                if resp.status_code == 200:
                    content = self._parse(resp)
                    log.info("LLM 成功：%d 字符，耗时 %.2fs", len(content), elapsed)
                    return content

                body = resp.text[:300]
                last_error = f"HTTP {resp.status_code}：{body}"
                log.warning("LLM 返回 %s：%s", resp.status_code, body)
                # 4xx 里除了限流都属于配置问题，重试没意义
                if resp.status_code not in (408, 409, 425, 429, 500, 502, 503, 504):
                    raise LLMError(last_error)

            if attempt < cfg.max_retries:
                time.sleep(min(1.5 * (attempt + 1), 5.0))

        raise LLMError(last_error)

    def _parse(self, resp: requests.Response) -> str:
        try:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise LLMError(f"无法解析模型响应：{exc}") from exc
        usage = data.get("usage")
        if isinstance(usage, dict):
            self.last_usage = usage
        if not isinstance(content, str) or not content.strip():
            raise LLMError("模型返回了空内容")
        return content.strip()

    def close(self) -> None:
        self._session.close()
