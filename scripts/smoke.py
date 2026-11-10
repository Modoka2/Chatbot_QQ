"""本地自检脚本：不依赖 QQ / 公众号，直接打本服务的接口。

用法：
    python scripts/smoke.py                          # 默认 http://127.0.0.1:8080
    python scripts/smoke.py http://127.0.0.1:8099
    python scripts/smoke.py --token 你的公众号Token    # 同时校验签名逻辑
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "self_id": 10000,
    "user_id": 20001,
    "message_id": int(time.time() * 1000) % 10_000_000,
    "sender": {"nickname": "自检用户"},
    "message": [{"type": "text", "data": {"text": "你好，做个自我介绍吧"}}],
}

GROUP_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "group_id": 555,
    "self_id": 10000,
    "user_id": 20002,
    "message_id": int(time.time() * 1000) % 10_000_000 + 1,
    "sender": {"nickname": "自检用户", "card": ""},
    "message": [
        {"type": "at", "data": {"qq": "10000"}},
        {"type": "text", "data": {"text": " 群里 @ 你一下"}},
    ],
}

WECHAT_XML = """<xml>
<ToUserName><![CDATA[gh_selfcheck]]></ToUserName>
<FromUserName><![CDATA[oSelfCheck]]></FromUserName>
<CreateTime>{ts}</CreateTime>
<MsgType><![CDATA[text]]></MsgType>
<Content><![CDATA[公众号自检：你好]]></Content>
<MsgId>{mid}</MsgId>
</xml>"""


def http(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        return 0, f"无法连接：{exc.reason}"


def wechat_signature(token: str, timestamp: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, timestamp, nonce])).encode()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="聊天机器人接口自检")
    parser.add_argument("base", nargs="?", default="http://127.0.0.1:8080")
    parser.add_argument("--token", default="", help="公众号 Token，填了就顺带验证签名")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ok = True
    print(f"自检目标：{base}\n")

    status, body = http(f"{base}/healthz")
    print(f"[1] GET  /healthz        -> {status} {body[:160]}")
    ok &= status == 200

    status, body = http(f"{base}/")
    print(f"[2] GET  /               -> {status} {'页面正常' if '聊天机器人' in body else '内容异常'}")
    ok &= status == 200 and "聊天机器人" in body

    status, body = http(
        f"{base}/onebot11/event",
        method="POST",
        data=json.dumps(PRIVATE_EVENT).encode(),
        headers={"Content-Type": "application/json"},
    )
    print(f"[3] POST /onebot11/event 私聊 -> {status} {body[:160]}")
    ok &= status == 200

    status, body = http(
        f"{base}/onebot11/event",
        method="POST",
        data=json.dumps(GROUP_EVENT).encode(),
        headers={"Content-Type": "application/json"},
    )
    print(f"[4] POST /onebot11/event 群聊@ -> {status} {body[:160]}")
    ok &= status == 200

    ts = str(int(time.time()))
    nonce = "selfcheck"
    query = {"timestamp": ts, "nonce": nonce, "echostr": "verify-ok"}
    if args.token:
        query["signature"] = wechat_signature(args.token, ts, nonce)
    status, body = http(f"{base}/wechat_mp?{urllib.parse.urlencode(query)}")
    print(f"[5] GET  /wechat_mp 校验 -> {status} {body[:80]}")
    ok &= status == 200

    ts = str(int(time.time()))
    mid = str(int(time.time() * 1000))
    query = {"timestamp": ts, "nonce": nonce}
    if args.token:
        query["signature"] = wechat_signature(args.token, ts, nonce)
    status, body = http(
        f"{base}/wechat_mp?{urllib.parse.urlencode(query)}",
        method="POST",
        data=WECHAT_XML.format(ts=ts, mid=mid).encode(),
        headers={"Content-Type": "application/xml"},
    )
    print(f"[6] POST /wechat_mp 消息 -> {status} {body[:200]}")
    ok &= status == 200

    print("\n结论：" + ("全部通过 ✅" if ok else "有失败项 ❌（看上面哪一步不为 200）"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
