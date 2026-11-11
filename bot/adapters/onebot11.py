"""OneBot v11 适配器（QQ）。

适用于 NapCat / LLOneBot / Lagrange.OneBot / go-cqhttp 等实现了
OneBot v11 协议的 QQ 端，通信方式固定为「HTTP 上报 + HTTP API 发送」：

    QQ端 --POST /onebot11/event--> 本服务 --POST /send_group_msg--> QQ端

事件解析只依赖公开协议字段，不绑定某一个实现。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import re
from pathlib import Path
from typing import Any

import requests

from ..config import OneBot11Config
from ..models import Message

log = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000

_PLACEHOLDERS = {
    "image": "[图片]",
    "mface": "[表情]",
    "face": "[表情]",
    "record": "[语音]",
    "video": "[视频]",
    "file": "[文件]",
    "json": "[卡片]",
    "xml": "[卡片]",
    "poke": "[戳一戳]",
    "dice": "[骰子]",
    "rps": "[猜拳]",
}

_CQ_RE = re.compile(r"\[CQ:([a-zA-Z_]+)((?:,[^\]]*)?)\]")
_AT_QQ_RE = re.compile(r"\[CQ:at,qq=([^,\]]+)")


def verify_signature(secret: str, body: bytes, signature: str) -> bool:
    """校验 OneBot v11 HTTP 上报签名（HMAC-SHA1，头 X-Signature: sha1=...）。"""
    if not secret:
        return True
    if not signature:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    given = signature.rsplit("=", 1)[-1].strip().lower()
    return hmac.compare_digest(expected, given)


def _cq_params(raw: str) -> dict[str, str]:
    """解析 CQ 码参数，例如 `,url=https://x.jpg,file=abc`。"""
    params: dict[str, str] = {}
    for item in raw.split(","):
        if "=" in item:
            key, _, value = item.partition("=")
            params[key.strip()] = value.replace("&#44;", ",").strip()
    return params


def _segments_to_text(segments: Any, self_ids: set[str]) -> tuple[str, bool, list[str]]:
    """把消息段转成 (纯文本, 是否@我, 图片地址列表)。"""
    if not segments:
        return "", False, []

    if isinstance(segments, str):
        at_me = bool(set(_AT_QQ_RE.findall(segments)) & self_ids)
        images: list[str] = []
        for match in _CQ_RE.finditer(segments):
            if match.group(1) == "image":
                params = _cq_params(match.group(2))
                ref = params.get("url") or params.get("file") or ""
                if ref:
                    images.append(ref)
        if at_me:
            # 去掉 @机器人 的 CQ 码，其余 CQ 码替换为占位符
            text = _CQ_RE.sub(lambda m: "" if m.group(1) == "at" else _PLACEHOLDERS.get(m.group(1), ""), segments)
        else:
            text = _CQ_RE.sub(lambda m: _PLACEHOLDERS.get(m.group(1), ""), segments)
        return text.strip(), at_me, images

    parts: list[str] = []
    at_me = False
    images = []
    for segment in segments:
        if isinstance(segment, str):
            parts.append(segment)
            continue
        if not isinstance(segment, dict):
            continue
        seg_type = segment.get("type")
        data = segment.get("data") or {}
        if seg_type == "text":
            parts.append(str(data.get("text", "")))
        elif seg_type == "at":
            qq = str(data.get("qq", ""))
            if qq in self_ids:
                at_me = True
            elif qq == "all":
                parts.append("@全体成员 ")
        else:
            parts.append(_PLACEHOLDERS.get(str(seg_type), ""))
            if seg_type == "image":
                ref = str(data.get("url") or data.get("file") or "")
                if ref:
                    images.append(ref)
    return "".join(parts).strip(), at_me, images


def parse_event(payload: dict[str, Any], self_ids: set[str] | None = None) -> Message | None:
    """把 OneBot v11 事件转成统一消息；不关心的事件返回 None。"""
    if not isinstance(payload, dict) or payload.get("post_type") != "message":
        return None
    if payload.get("message_type") == "message_sent":  # 机器人自己发的
        return None

    ids = {str(x) for x in (self_ids or set()) if x}
    self_id = str(payload.get("self_id") or "")
    if self_id:
        ids.add(self_id)

    user_id = str(payload.get("user_id") or "")
    if not user_id:
        return None
    # 机器人自己发的消息一律忽略，避免自问自答死循环
    if self_id and user_id == self_id:
        return None

    sender = payload.get("sender") or {}
    name = str(sender.get("card") or sender.get("nickname") or user_id)

    message_type = payload.get("message_type")
    if message_type == "group":
        group_id = str(payload.get("group_id") or "")
        if not group_id:
            return None
        chat_type, chat_id = "group", group_id
    elif message_type == "private":
        chat_type, chat_id = "private", user_id
    else:
        return None

    text, at_me, images = _segments_to_text(payload.get("message"), ids)
    if not text and not images:
        raw_message = payload.get("raw_message")
        if raw_message:
            text, at_me, images = _segments_to_text(str(raw_message), ids)

    return Message(
        platform="onebot11",
        chat_type=chat_type,
        chat_id=chat_id,
        user_id=user_id,
        user_name=name,
        text=text[:MAX_TEXT_CHARS],
        at_me=at_me,
        message_id=str(payload.get("message_id") or ""),
        images=images[:5],
        raw=payload,
    )


def _as_id(value: str) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


class OneBot11Client:
    def __init__(self, config: OneBot11Config, session: requests.Session | None = None) -> None:
        self.config = config
        self._session = session or requests.Session()
        self._base = config.api_base.rstrip("/")
        self._headers = {"Content-Type": "application/json"}
        if config.access_token:
            self._headers["Authorization"] = f"Bearer {config.access_token}"

    def send_text(self, msg: Message, text: str) -> bool:
        """发送文本。"""
        return self._send(msg, [{"type": "text", "data": {"text": text}}])

    def send_image(self, msg: Message, path: str) -> bool:
        """发送本地图片（表情包）。

        先用 `file://` 让 QQ 端直接读本地文件；失败（比如权限/路径问题）
        再退化成 `base64://` 内联发送，保证一定能发出去。
        """
        if not path:
            log.error("表情包路径为空")
            return False
        try:
            p = Path(path).resolve()
        except (OSError, ValueError) as exc:
            log.error("表情包路径无效：%s（%s）", path, exc)
            return False
        if not p.is_file():
            log.error("表情包不存在：%s", p)
            return False

        if self._send(msg, [{"type": "image", "data": {"file": p.as_uri()}}]):
            return True

        try:
            raw = p.read_bytes()
        except OSError as exc:
            log.error("读取表情包失败：%s", exc)
            return False
        if len(raw) > 4 * 1024 * 1024:
            return False
        log.info("file:// 发送失败，改用 base64 内联重试：%s", p.name)
        return self._send(
            msg,
            [{"type": "image", "data": {"file": "base64://" + base64.b64encode(raw).decode()}}],
        )

    def send_voice(self, msg: Message, path: str) -> bool:
        """发送语音（record）。先试 file://，失败退 base64。"""
        if not path:
            log.error("语音路径为空")
            return False
        try:
            p = Path(path).resolve()
        except (OSError, ValueError) as exc:
            log.error("语音路径无效：%s（%s）", path, exc)
            return False
        if not p.is_file():
            log.error("语音文件不存在：%s", p)
            return False

        if self._send(msg, [{"type": "record", "data": {"file": p.as_uri()}}]):
            return True

        try:
            raw = p.read_bytes()
        except OSError as exc:
            log.error("读取语音失败：%s", exc)
            return False
        if len(raw) > 4 * 1024 * 1024:
            return False
        log.info("语音 file:// 发送失败，改用 base64 重试：%s", p.name)
        return self._send(
            msg,
            [{"type": "record", "data": {"file": "base64://" + base64.b64encode(raw).decode()}}],
        )

    def _send(self, msg: Message, segments: list[dict[str, Any]]) -> bool:
        """按会话类型发送消息段，返回是否成功。"""
        if msg.chat_type == "group":
            action = "send_group_msg"
            params = {"group_id": _as_id(msg.chat_id)}
        else:
            action = "send_private_msg"
            params = {"user_id": _as_id(msg.user_id)}

        # 用消息段数组而不是字符串，避免 CQ 码注入
        params["message"] = segments
        url = f"{self._base}/{action}"

        try:
            resp = self._session.post(
                url, json=params, headers=self._headers, timeout=(5, self.config.timeout)
            )
        except requests.RequestException as exc:
            log.error("OneBot 发送失败（%s）：%s", action, exc)
            return False

        if resp.status_code != 200:
            log.error("OneBot 发送失败：HTTP %s %s", resp.status_code, resp.text[:200])
            return False

        try:
            data = resp.json()
        except ValueError:
            return True
        if isinstance(data, dict) and (data.get("status") == "failed" or data.get("retcode")):
            log.error("OneBot 返回失败：%s", data)
            return False
        return True

    def close(self) -> None:
        self._session.close()
