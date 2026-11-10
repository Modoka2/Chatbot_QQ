"""本地全链路联调：不需要 API Key，也不需要真的 QQ / 公众号。

它会起三个东西：
  1. 假模型服务（OpenAI 兼容，真实 HTTP）+  故意返回 500 的失败路径
  2. 假 OneBot 服务（记录机器人发出去的每一条消息）
  3. 真的机器人服务（bot.server.create_app + werkzeug 监听随机端口）

然后用真实 HTTP 请求打一整圈：QQ 私聊 -> 群聊 @ -> 公众号验证 -> 公众号消息，
并校验模型调用参数、鉴权头、重试、超时、去重、记忆落库。

用法：
    python scripts/e2e_local.py
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from werkzeug.serving import make_server  # noqa: E402

from bot.config import AppConfig  # noqa: E402
from bot.server import create_app  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASSED if cond else FAILED).append(name)
    print(f"  {'✅' if cond else '❌'} {name}" + (f"  ({detail})" if detail and not cond else ""))


# ---------------------------------------------------------------- 假模型
class StubLLM(BaseHTTPRequestHandler):
    calls: list[dict] = []
    fail_times = 0  # 前 N 次故意返回 500，用来验证重试

    def log_message(self, *args):  # 关掉噪音日志
        pass

    def handle_error(self, *args):  # 客户端超时断开时不打印堆栈
        pass

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        StubLLM.calls.append(
            {"path": self.path, "auth": self.headers.get("Authorization", ""), "payload": payload}
        )

        messages = payload.get("messages") or [{}]
        user_text = messages[-1].get("content", "")

        if StubLLM.fail_times > 0:
            StubLLM.fail_times -= 1
            self._json(500, {"error": {"message": "stub 故意失败"}})
            return
        if "超时测试" in user_text:
            time.sleep(3.0)
        if "报错测试" in user_text:
            self._json(500, {"error": {"message": "stub 故意失败"}})
            return

        self._json(
            200,
            {
                "id": "chatcmpl-stub",
                "object": "chat.completion",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": f"本地假模型收到：{user_text}"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"total_tokens": 10},
            },
        )


# ---------------------------------------------------------------- 假 OneBot
class StubOneBot(BaseHTTPRequestHandler):
    sent: list[dict] = []

    def log_message(self, *args):
        pass

    def handle_error(self, *args):
        pass

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            payload = {}
        StubOneBot.sent.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization", ""),
                "payload": payload,
            }
        )
        body = json.dumps({"status": "ok", "retcode": 0, "data": {"message_id": 1}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


# ---------------------------------------------------------------- 工具
class QuietServer(ThreadingHTTPServer):
    """客户端超时断开时不往控制台喷堆栈。"""

    daemon_threads = True

    def handle_error(self, request, client_address):
        pass


def serve(handler_cls) -> QuietServer:
    server = QuietServer(("127.0.0.1", 0), handler_cls)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def http(url: str, method: str = "GET", data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def wechat_sig(token: str, ts: str, nonce: str) -> str:
    return hashlib.sha1("".join(sorted([token, ts, nonce])).encode()).hexdigest()


PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "self_id": 10000,
    "user_id": 20001,
    "message_id": 900001,
    "sender": {"nickname": "小明"},
    "message": [{"type": "text", "data": {"text": "你好，介绍一下你自己"}}],
}

GROUP_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "group_id": 555,
    "self_id": 10000,
    "user_id": 20002,
    "message_id": 900002,
    "sender": {"nickname": "小红", "card": "小红帽"},
    "message": [
        {"type": "at", "data": {"qq": "10000"}},
        {"type": "text", "data": {"text": " 帮我想个晚饭"}},
    ],
}

GROUP_NO_AT_EVENT = {
    "post_type": "message",
    "message_type": "group",
    "group_id": 555,
    "self_id": 10000,
    "user_id": 20003,
    "message_id": 900003,
    "sender": {"nickname": "路人"},
    "message": [{"type": "text", "data": {"text": "你们在聊什么"}}],
}

WECHAT_XML = """<xml><ToUserName><![CDATA[gh_stub]]></ToUserName>
<FromUserName><![CDATA[oStubUser]]></FromUserName><CreateTime>1700000000</CreateTime>
<MsgType><![CDATA[text]]></MsgType><Content><![CDATA[公众号里问一句]]></Content>
<MsgId>998877</MsgId></xml>"""


def main() -> int:
    llm_server = serve(StubLLM)
    onebot_server = serve(StubOneBot)
    llm_port = llm_server.server_address[1]
    onebot_port = onebot_server.server_address[1]

    tmpdir = Path(tempfile.mkdtemp(prefix="bot-e2e-"))
    config = AppConfig.model_validate(
        {
            "server": {"log_level": "critical"},
            "llm": {
                "provider": "openai",
                "base_url": f"http://127.0.0.1:{llm_port}/v1",
                "api_key": "sk-e2e-test",
                "model": "stub-model",
                "timeout": 1.0,
                "max_retries": 1,
                "history_window": 16,
                "system_prompt": "你是联调用的助手。",
            },
            "memory": {"path": str(tmpdir / "e2e.db"), "ttl_minutes": 0, "max_turns": 24},
            "reply": {
                "group_prefixes": ["/ai"],
                "rate_limit": {"per_user_per_minute": 0, "per_chat_per_minute": 0},
            },
            "onebot11": {
                "enabled": True,
                "api_base": f"http://127.0.0.1:{onebot_port}",
                "access_token": "tok-e2e",
                "async_reply": False,
            },
            "sticker": {"enabled": True, "dir": "stickers", "probability": 0},
            "search": {"enabled": False},
            "wechat_mp": {"enabled": True, "token": "e2e-token", "reply_timeout": 4.5},
        }
    )

    app = create_app(config)
    memory = app.extensions["bot"]["memory"]
    bot_server = make_server("127.0.0.1", 0, app, threaded=True)
    bot_port = bot_server.socket.getsockname()[1]
    threading.Thread(target=bot_server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{bot_port}"

    print(f"假模型 :{llm_port}   假 OneBot :{onebot_port}   机器人 :{bot_port}\n")
    try:
        print("[1] 基础接口")
        status, body = http(f"{base}/healthz")
        check("健康检查 200", status == 200)
        check("健康检查返回 provider=openai", '"provider":"openai"' in body, body[:80])

        print("\n[2] QQ 私聊（真实 HTTP 打到假模型）")
        status, body = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(PRIVATE_EVENT).encode(),
            {"Content-Type": "application/json"},
        )
        check("私聊事件 200 且已处理", status == 200 and '"status":"ok"' in body, body[:120])
        check("假模型收到了调用", len(StubLLM.calls) == 1, f"{len(StubLLM.calls)} 次")
        call = StubLLM.calls[-1]
        check("请求路径是 /v1/chat/completions", call["path"].endswith("/v1/chat/completions"), call["path"])
        check("带了 Bearer 鉴权头", call["auth"] == "Bearer sk-e2e-test", call["auth"])
        check("模型名透传正确", call["payload"].get("model") == "stub-model", str(call["payload"].get("model")))
        check(
            "system prompt 已注入",
            call["payload"]["messages"][0]["content"].startswith("你是联调用的助手。"),
            call["payload"]["messages"][0]["content"][:40],
        )

        check("OneBot 收到了私聊回复", len(StubOneBot.sent) == 1, f"{len(StubOneBot.sent)} 条")
        sent = StubOneBot.sent[-1]
        check("调用的是 send_private_msg", sent["path"].endswith("/send_private_msg"), sent["path"])
        check("发送带 access_token", sent["auth"] == "Bearer tok-e2e", sent["auth"])
        private_text = sent["payload"]["message"][0]["data"]["text"]
        check("回复内容来自模型", "本地假模型收到" in private_text, private_text[:60])

        print("\n[3] QQ 群聊")
        before = len(StubOneBot.sent)
        status, body = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(GROUP_EVENT).encode(),
            {"Content-Type": "application/json"},
        )
        check("群聊 @ 事件已处理", status == 200)
        check("群聊回了一条", len(StubOneBot.sent) == before + 1)
        group_sent = StubOneBot.sent[-1]
        check("调用的是 send_group_msg", group_sent["path"].endswith("/send_group_msg"), group_sent["path"])
        check("group_id 正确", group_sent["payload"].get("group_id") == 555, str(group_sent["payload"].get("group_id")))
        check(
            "群聊上下文带上了昵称",
            "小红帽：帮我想个晚饭" in StubLLM.calls[-1]["payload"]["messages"][-1]["content"],
            StubLLM.calls[-1]["payload"]["messages"][-1]["content"],
        )

        print("\n[4] 群聊没 @ 应该保持沉默")
        before = len(StubOneBot.sent)
        model_before = len(StubLLM.calls)
        status, body = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(GROUP_NO_AT_EVENT).encode(),
            {"Content-Type": "application/json"},
        )
        check("没 @ 也返回 200（但不能让 QQ 端重试）", status == 200)
        check("没有调用模型", len(StubLLM.calls) == model_before)
        check("没有发消息", len(StubOneBot.sent) == before)

        print("\n[5] 重复事件去重")
        status, body = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(GROUP_EVENT).encode(),
            {"Content-Type": "application/json"},
        )
        check("重复事件被丢弃", '"status":"duplicate"' in body, body[:80])

        print("\n[6] 上下文记忆（在同一个群里再说一句）")
        http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(
                {
                    **GROUP_EVENT,
                    "message_id": 900004,
                    "message": [
                        {"type": "at", "data": {"qq": "10000"}},
                        {"type": "text", "data": {"text": " 那吃面行不行"}},
                    ],
                }
            ).encode(),
            {"Content-Type": "application/json"},
        )
        turn = StubLLM.calls[-1]["payload"]["messages"]
        check("群聊第二句带上了上一轮", len(turn) == 4, f"{len(turn)} 条")
        check(
            "历史里有上一轮的问答",
            any("本地假模型收到" in m["content"] for m in turn[1:]),
            json.dumps(turn[1:], ensure_ascii=False)[:160],
        )
        check(
            "记忆按会话隔离（私聊历史不会串进群聊）",
            all("介绍一下你自己" not in m["content"] for m in turn),
            json.dumps(turn, ensure_ascii=False)[:160],
        )

        print("\n[7] 公众号")
        ts, nonce = str(int(time.time())), "e2e"
        q = {"signature": wechat_sig("e2e-token", ts, nonce), "timestamp": ts, "nonce": nonce}
        status, body = http(f"{base}/wechat_mp?{urllib.parse.urlencode({**q, 'echostr': 'ok-e2e'})}")
        check("服务器地址验证通过", status == 200 and body == "ok-e2e", f"{status} {body[:40]}")

        status, body = http(
            f"{base}/wechat_mp?{urllib.parse.urlencode(q)}",
            "POST",
            WECHAT_XML.encode(),
            {"Content-Type": "application/xml"},
        )
        check("公众号消息被动回复 200", status == 200, str(status))
        check("回复是 XML 且带 CDATA 内容", "<xml>" in body and "本地假模型收到" in body, body[:120])
        check("ToUserName 是用户 OpenID", "<ToUserName><![CDATA[oStubUser]]></ToUserName>" in body)

        bad_q = {**q, "signature": "wrong"}
        status, _ = http(f"{base}/wechat_mp?{urllib.parse.urlencode(bad_q)}")
        check("错误签名被拒绝 403", status == 403, str(status))

        print("\n[8] 模型 500 错误")
        event = {**PRIVATE_EVENT, "message_id": 900010, "message": [{"type": "text", "data": {"text": "报错测试"}}]}
        model_before = len(StubLLM.calls)
        status, _ = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(event).encode(),
            {"Content-Type": "application/json"},
        )
        attempts = len(StubLLM.calls) - model_before
        check("失败后重试了一次（共 2 次）", attempts == 2, f"{attempts} 次")
        check("给用户回了兜底话术", "抱歉" in StubOneBot.sent[-1]["payload"]["message"][0]["data"]["text"])
        check(
            "失败的对话没有写进记忆",
            all("报错测试" not in item["content"] for item in memory.load("onebot11:private:20001")),
        )

        print("\n[9] 模型超时（read timeout=1s）")
        event = {**PRIVATE_EVENT, "message_id": 900011, "message": [{"type": "text", "data": {"text": "超时测试"}}]}
        started = time.time()
        status, _ = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(event).encode(),
            {"Content-Type": "application/json"},
        )
        elapsed = time.time() - started
        check("超时没有把请求挂死", status == 200 and elapsed < 8, f"{elapsed:.1f}s")
        check("超时也回了兜底话术", "抱歉" in StubOneBot.sent[-1]["payload"]["message"][0]["data"]["text"])

        print("\n[10] 指令与记忆清理")
        status, body = http(
            f"{base}/onebot11/event",
            "POST",
            json.dumps(
                {**PRIVATE_EVENT, "message_id": 900012, "message": [{"type": "text", "data": {"text": "/reset"}}]}
            ).encode(),
            {"Content-Type": "application/json"},
        )
        check("/reset 清空并回复", "已清空" in StubOneBot.sent[-1]["payload"]["message"][0]["data"]["text"])
        check("记忆确实清空了", memory.load("onebot11:private:20001") == [])
    finally:
        bot_server.shutdown()
        llm_server.shutdown()
        onebot_server.shutdown()

    print("\n" + "=" * 50)
    print(f"通过 {len(PASSED)} 项，失败 {len(FAILED)} 项")
    if FAILED:
        print("失败：" + "、".join(FAILED))
        return 1
    print("本地全链路联调通过，链路是通的。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
