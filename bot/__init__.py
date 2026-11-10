"""微信 / QQ AI 聊天机器人。

模块划分：
- config   配置加载
- models   统一消息模型
- memory   会话记忆（SQLite）
- llm      大模型调用（OpenAI 兼容协议）
- handler  消息处理核心（过滤 / 限流 / 指令 / 生成）
- adapters 各平台协议适配（OneBot v11 / 微信公众号）
- server   HTTP 服务
"""

__version__ = "0.1.0"
