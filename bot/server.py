"""HTTP 服务层（Flask，同步模型）。

路由：
    GET  /                 浏览器里看一眼运行状态
    GET  /healthz          健康检查（给监控 / 容器用）
    POST /onebot11/event   QQ 端 HTTP 上报入口
    GET  /wechat_mp        公众号服务器地址校验
    POST /wechat_mp        公众号消息推送 + 被动回复

为什么 QQ 走后台线程：OneBot 的 HTTP 上报默认 5 秒超时，模型要 3~20 秒，
同步等会让 QQ 端不断重试。所以立刻回 200，回复在后台发出去。
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout

from flask import Flask, Response, jsonify, request

from . import __version__
from .adapters.onebot11 import OneBot11Client, parse_event, verify_signature
from .adapters.wechat_mp import (
    WeChatMPClient,
    build_text_reply,
    check_signature,
    parse_message,
)
from .config import AppConfig
from .handler import ChatHandler
from .llm import LLMClient
from .memory import ConversationMemory
from .models import Reply

log = logging.getLogger(__name__)


class Deduplicator:
    """丢弃平台重试造成的重复事件（OneBot 超时会重发同一个 message_id）。"""

    def __init__(self, ttl: float = 180.0, max_items: int = 4096) -> None:
        self.ttl = ttl
        self.max_items = max_items
        self._seen: OrderedDict[str, float] = OrderedDict()
        self._lock = threading.Lock()

    def is_duplicate(self, key: str) -> bool:
        if not key:
            return False
        now = time.monotonic()
        with self._lock:
            while self._seen:
                oldest_key, oldest_at = next(iter(self._seen.items()))
                if now - oldest_at <= self.ttl and len(self._seen) < self.max_items:
                    break
                self._seen.popitem(last=False)
            if key in self._seen:
                return True
            self._seen[key] = now
            return False

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


def create_app(config: AppConfig | None = None) -> Flask:
    config = config or AppConfig.load()
    logging.basicConfig(
        level=getattr(logging, config.server.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )

    memory = ConversationMemory(
        config.memory.path,
        ttl_minutes=config.memory.ttl_minutes,
        max_turns=config.memory.max_turns,
    )
    llm = LLMClient(config.llm)
    handler = ChatHandler(config, llm, memory)
    onebot = OneBot11Client(config.onebot11)
    wechat = WeChatMPClient(config.wechat_mp)
    dedupe = Deduplicator()
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="wechat")
    started_at = time.time()

    app = Flask(__name__)
    app.json.ensure_ascii = False
    app.extensions["bot"] = {
        "config": config,
        "handler": handler,
        "memory": memory,
        "llm": llm,
        "onebot11": onebot,
        "wechat_mp": wechat,
        "dedupe": dedupe,
        "started_at": started_at,
    }

    # ---------------------------------------------------------------- 通用
    @app.get("/healthz")
    def healthz():
        return jsonify(
            status="ok",
            version=__version__,
            uptime_seconds=int(time.time() - started_at),
            memory=memory.stats(),
            guard=handler.guard.snapshot(),
            paused=handler.paused,
            provider=config.llm.provider,
            model=config.llm.model,
        )

    @app.get("/")
    def index():
        stats = memory.stats()
        html = f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>微信 / QQ AI 聊天机器人</title>
<style>
 body{{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:720px;margin:48px auto;padding:0 20px;color:#1f2328}}
 h1{{font-size:22px}} code{{background:#f3f4f6;padding:2px 6px;border-radius:4px}}
 li{{margin:6px 0}} .ok{{color:#1a7f37;font-weight:600}}
</style></head><body>
<h1>微信 / QQ AI 聊天机器人</h1>
<p class="ok">服务运行中，已稳定运行 {int(time.time() - started_at)} 秒。</p>
<ul>
  <li>QQ 上报地址：<code>POST /onebot11/event</code></li>
  <li>公众号回调地址：<code>GET|POST /wechat_mp</code></li>
  <li>健康检查：<code>GET /healthz</code></li>
</ul>
<p>模型：<code>{config.llm.provider} / {config.llm.model}</code><br>
记忆库：{stats['sessions']} 个会话，{stats['turns']} 条消息</p>
</body></html>"""
        return Response(html, mimetype="text/html; charset=utf-8")

    # ---------------------------------------------------------------- QQ
    @app.post("/onebot11/event")
    def onebot_event():
        raw = request.get_data(cache=False)
        signature = request.headers.get("X-Signature", "")
        if not config.onebot11.enabled:
            return jsonify(status="disabled"), 200
        if not verify_signature(config.onebot11.secret, raw, signature):
            log.warning("OneBot 签名校验失败，来源 %s", request.remote_addr)
            return jsonify(status="bad_signature"), 403

        try:
            payload = json.loads(raw.decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return jsonify(status="bad_json"), 400

        msg = parse_event(payload, set(config.onebot11.self_ids))
        if msg is None:
            return jsonify(status="ignored")

        key = msg.message_id or hashlib.sha1(raw).hexdigest()
        if dedupe.is_duplicate(f"onebot:{key}"):
            return jsonify(status="duplicate")

        def work() -> None:
            try:
                out = handler.reply(msg)
                if out is None or out.is_empty():
                    return
                if out.text:
                    onebot.send_text(msg, out.text)
                if out.sticker:
                    onebot.send_image(msg, out.sticker)
                if out.voice:
                    onebot.send_voice(msg, out.voice)
            except Exception:  # 后台线程里的异常绝不能吞掉日志
                log.exception("处理 QQ 消息失败")

        if config.onebot11.async_reply:
            threading.Thread(target=work, daemon=True, name="onebot-reply").start()
            return jsonify(status="queued")
        work()
        return jsonify(status="ok")

    # ---------------------------------------------------------------- 公众号
    @app.get(config.wechat_mp.path)
    def wechat_verify():
        if not config.wechat_mp.enabled:
            return Response("disabled", status=404)
        args = request.args
        if not check_signature(
            config.wechat_mp.token,
            args.get("signature", ""),
            args.get("timestamp", ""),
            args.get("nonce", ""),
        ):
            log.warning("公众号签名校验失败，来源 %s", request.remote_addr)
            return Response("invalid signature", status=403, mimetype="text/plain")
        return Response(args.get("echostr", ""), mimetype="text/plain")

    @app.post(config.wechat_mp.path)
    def wechat_message():
        if not config.wechat_mp.enabled:
            return Response("disabled", status=404)

        raw = request.get_data(cache=False)
        args = request.args
        if not check_signature(
            config.wechat_mp.token,
            args.get("signature", ""),
            args.get("timestamp", ""),
            args.get("nonce", ""),
        ):
            return Response("invalid signature", status=403, mimetype="text/plain")

        incoming = parse_message(raw)
        if incoming is None or not incoming.from_user:
            return Response("success", mimetype="text/plain")

        # 事件推送、图片、图文等类型先不处理，直接 ack，避免公众号提示「无法提供服务」
        if incoming.event or incoming.msg_type not in ("text", "voice", "image"):
            return Response("success", mimetype="text/plain")
        if not incoming.content.strip() and not incoming.recognition.strip() and not incoming.pic_url:
            return Response("success", mimetype="text/plain")

        msg = incoming.to_message()
        future = pool.submit(handler.reply, msg)
        try:
            out = future.result(timeout=config.wechat_mp.reply_timeout)
        except FutureTimeout:
            log.warning("公众号回复超时，先返回兜底话")
            out = Reply(text=config.wechat_mp.timeout_text)
            if wechat.can_push:

                def push_late() -> None:
                    try:
                        late = future.result(timeout=180)
                    except Exception:
                        log.exception("后台等待模型结果失败")
                        return
                    if late and late.text:
                        wechat.send_custom_text(incoming.from_user, late.text)
                        log.info("已通过客服消息补发答案：%s", incoming.from_user)

                threading.Thread(target=push_late, daemon=True, name="wechat-push").start()
        except Exception:
            log.exception("处理公众号消息失败")
            out = Reply(text="服务开小差了，请稍后再试。")

        if not out or not out.text:
            return Response("success", mimetype="text/plain")

        xml = build_text_reply(incoming.from_user, incoming.to_user, out.text)
        return Response(xml, mimetype="application/xml; charset=utf-8")

    return app
