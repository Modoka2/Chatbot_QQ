"""启动入口。

用法：
    python main.py                      # 读取 config.local.yaml / config.yaml
    python main.py --mock               # 不调真实模型，用于本地联调
    python main.py --port 9000
"""

from __future__ import annotations

import argparse
import logging
import socket
import sys
import time

from bot.config import AppConfig
from bot.server import create_app

log = logging.getLogger("bot")


def _lan_ip() -> str:
    try:
        return socket.gethostbyname_ex(socket.gethostname())[2][-1]
    except OSError:
        return "127.0.0.1"


def _banner(config: AppConfig) -> str:
    host = "127.0.0.1" if config.server.host in ("0.0.0.0", "") else config.server.host
    port = config.server.port
    ip = _lan_ip()
    lines = [
        "=" * 62,
        "  微信 / QQ AI 聊天机器人  已启动",
        "=" * 62,
        f"  监听地址     http://{config.server.host}:{port}",
        f"  本机访问     http://127.0.0.1:{port}",
        f"  局域网访问   http://{ip}:{port}",
        "",
        f"  QQ 上报      http://{ip}:{port}/onebot11/event   (填入 OneBot HTTP 上报地址)",
        f"  公众号回调   https://你的域名{config.wechat_mp.path}   (需公网 HTTPS)",
        f"  健康检查     http://127.0.0.1:{port}/healthz",
        "",
        f"  模型         {config.llm.provider} / {config.llm.model}",
        f"  记忆库       {config.memory.path}",
        "=" * 62,
        "  按 Ctrl+C 退出",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="微信 / QQ AI 聊天机器人")
    parser.add_argument("-c", "--config", help="配置文件路径，默认 config.local.yaml 或 config.yaml")
    parser.add_argument("--host", help="监听地址，覆盖配置文件")
    parser.add_argument("--port", type=int, help="监听端口，覆盖配置文件")
    parser.add_argument("--mock", action="store_true", help="强制使用 mock 模型（无需 API Key）")
    parser.add_argument("--check", action="store_true", help="只检查配置是否合法，然后退出")
    parser.add_argument("--probe", action="store_true", help="真实调用一次模型，验证 Key / 网络是否可用")
    args = parser.parse_args(argv)

    try:
        config = AppConfig.load(args.config)
    except Exception as exc:
        print(f"[配置错误] {exc}", file=sys.stderr)
        return 2

    if args.mock:
        config.llm.provider = "mock"
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port

    if args.check or args.probe:
        print("配置校验通过。")
        print(f"  QQ       : {'启用' if config.onebot11.enabled else '关闭'}")
        print(f"  公众号   : {'启用' if config.wechat_mp.enabled else '关闭'}")
        print(f"  模型     : {config.llm.provider} / " + ("mock（假回复）" if config.llm.provider == "mock" else config.llm.model))
        print(f"  接口密钥 : {'已配置' if config.llm.api_key else '未配置'}")
        if not args.probe:
            return 0

    if args.probe:
        from bot.llm import LLMClient, LLMError

        if config.llm.provider == "mock":
            print("\n当前是 mock 模式，不会发起真实请求。")
            return 0

        client = LLMClient(config.llm)
        print(f"\n正在真实调用 {config.llm.model} …")
        started = time.time()
        try:
            reply = client.chat([{"role": "user", "content": "只回复两个字：通了"}])
        except LLMError as exc:
            print(f"[失败] {exc}", file=sys.stderr)
            print("常见原因：Key 无效或欠费、base_url 写错、本机出网需要代理。", file=sys.stderr)
            return 1
        finally:
            client.close()
        print(f"[成功] 耗时 {time.time() - started:.2f}s，模型回复：{reply[:80]}")
        return 0

    app = create_app(config)
    print(_banner(config))
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    app.run(
        host=config.server.host,
        port=config.server.port,
        threaded=True,
        use_reloader=False,
        debug=False,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
