"""微信公众号适配器。

走公众号后台「服务器配置」的原始消息推送：

    GET  /wechat_mp  验证服务器地址（sha1 签名）
    POST /wechat_mp  接收用户消息，同步返回 XML 被动回复

限制与取舍：
- 被动回复必须 **5 秒** 内返回，所以 ``reply_timeout`` 默认 4.5 秒，
  超时先回一句兜底话；如果填了 app_id / app_secret，会用「客服消息」
  在后台把真正的答案补发过去（用户 48 小时内有互动即可送达）。
- 只支持 **明文模式**。安全模式的 AES 加解密需要第三方加密库，
  本机没装，也不影响使用：把公众号消息加解密方式设为明文即可。
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import requests

from ..config import WeChatMPConfig
from ..models import Message

log = logging.getLogger(__name__)

MAX_XML_BYTES = 256 * 1024


class WeChatError(RuntimeError):
    pass


def check_signature(token: str, signature: str, timestamp: str, nonce: str) -> bool:
    """公众号服务器地址校验：sha1(sort(token, timestamp, nonce))。"""
    if not token:
        return True
    if not (signature and timestamp and nonce):
        return False
    expected = hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode("utf-8")).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(slots=True)
class WeChatIncoming:
    to_user: str      # 公众号原始 ID（回复时的 FromUserName）
    from_user: str    # 用户 OpenID
    msg_type: str
    content: str = ""
    msg_id: str = ""
    create_time: int = 0
    event: str = ""
    recognition: str = ""
    pic_url: str = ""

    def to_message(self) -> Message:
        text = self.content.strip() or self.recognition.strip()
        return Message(
            platform="wechat_mp",
            chat_type="private",
            chat_id=self.from_user,
            user_id=self.from_user,
            user_name=self.from_user[:10],
            text=text,
            at_me=True,
            message_id=self.msg_id,
            images=[self.pic_url] if self.pic_url else [],
        )


def parse_message(body: bytes | str) -> WeChatIncoming | None:
    """解析推送 XML。无法识别（例如加密消息）时返回 None。"""
    if isinstance(body, str):
        body = body.encode("utf-8")
    if not body or len(body) > MAX_XML_BYTES:
        return None

    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        log.warning("公众号 XML 解析失败：%s", exc)
        return None

    if root.findtext("Encrypt"):
        log.error("收到加密模式消息，请把公众号「消息加解密方式」改为明文模式")
        return None

    msg_type = (root.findtext("MsgType") or "").strip()
    if not msg_type:
        return None

    try:
        create_time = int(root.findtext("CreateTime") or 0)
    except ValueError:
        create_time = 0

    return WeChatIncoming(
        to_user=(root.findtext("ToUserName") or "").strip(),
        from_user=(root.findtext("FromUserName") or "").strip(),
        msg_type=msg_type.lower(),
        content=(root.findtext("Content") or "").strip(),
        msg_id=(root.findtext("MsgId") or "").strip(),
        create_time=create_time,
        event=(root.findtext("Event") or "").strip().lower(),
        recognition=(root.findtext("Recognition") or "").strip(),
        pic_url=(root.findtext("PicUrl") or "").strip(),
    )


def _cdata(text: str) -> str:
    return "<![CDATA[" + text.replace("]]>", "]]]]><![CDATA[>") + "]]>"


def build_text_reply(to_user: str, from_user: str, content: str, create_time: int | None = None) -> str:
    """构造被动回复 XML。to_user 是用户 OpenID，from_user 是公众号 ID。"""
    ts = int(create_time if create_time is not None else time.time())
    content = content[:2000]
    return (
        "<xml>"
        f"<ToUserName>{_cdata(to_user)}</ToUserName>"
        f"<FromUserName>{_cdata(from_user)}</FromUserName>"
        f"<CreateTime>{ts}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content>{_cdata(content)}</Content>"
        "</xml>"
    )


class WeChatMPClient:
    TOKEN_URL = "https://api.weixin.qq.com/cgi-bin/token"
    SEND_URL = "https://api.weixin.qq.com/cgi-bin/message/custom/send"

    def __init__(self, config: WeChatMPConfig, session: requests.Session | None = None) -> None:
        self.config = config
        self._session = session or requests.Session()
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    @property
    def can_push(self) -> bool:
        """没配 app_id / app_secret 就只能走被动回复。"""
        return bool(self.config.app_id and self.config.app_secret)

    def _access_token(self) -> str:
        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - 60:
                return self._token
            resp = self._session.get(
                self.TOKEN_URL,
                params={
                    "grant_type": "client_credential",
                    "appid": self.config.app_id,
                    "secret": self.config.app_secret,
                },
                timeout=10,
            )
            data = resp.json()
            if "access_token" not in data:
                raise WeChatError(f"获取 access_token 失败：{data}")
            self._token = str(data["access_token"])
            self._expires_at = now + float(data.get("expires_in", 7200))
            return self._token

    def send_custom_text(self, openid: str, text: str) -> bool:
        """客服消息补发（需要认证服务号 / 测试号）。"""
        if not self.can_push:
            return False
        try:
            token = self._access_token()
            resp = self._session.post(
                self.SEND_URL,
                params={"access_token": token},
                json={"touser": openid, "msgtype": "text", "text": {"content": text[:2000]}},
                timeout=10,
            )
            data = resp.json()
        except (requests.RequestException, ValueError, WeChatError) as exc:
            log.error("客服消息发送失败：%s", exc)
            return False
        if data.get("errcode"):
            log.error("客服消息发送失败：%s", data)
            return False
        return True

    def close(self) -> None:
        self._session.close()
