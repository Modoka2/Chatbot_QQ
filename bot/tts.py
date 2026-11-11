"""语音合成（TTS）：把回复变成一条语音。

两个引擎，自动取舍：

- ``edge``：微软 Edge 在线语音（免费、自然，中文有 Xiaoxiao / Yunxi 等），需要联网
- ``sapi``：Windows 自带离线语音（Huihui 中文），断网也能用，音质一般

产物默认放 ``data/tts/``，发送成功后会顺手清理旧文件，避免越攒越多。
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
import uuid
from pathlib import Path

import requests

from .config import TTSConfig

log = logging.getLogger(__name__)


class TTSError(RuntimeError):
    pass


class TTSClient:
    def __init__(self, config: TTSConfig, session=None) -> None:
        self.config = config
        self.dir = Path(config.dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self._session = session
        self._http = session or requests.Session()

    # ---------------------------------------------------------------- 对外
    def synthesize(self, text: str) -> Path:
        """把文本合成语音文件，返回路径。"""
        text = (text or "").strip()
        if not text:
            raise TTSError("空文本")
        if len(text) > self.config.max_chars * 2:
            text = text[: self.config.max_chars * 2]

        engine = (self.config.engine or "edge").lower()
        handlers = {
            "sapi": self._sapi,
            "edge": self._edge,
            "openai": self._openai,
        }
        handler = handlers.get(engine, self._edge)
        try:
            return handler(text)
        except Exception as exc:
            log.warning("%s 合成失败：%s", engine, exc)
            if engine == "sapi":
                raise TTSError(str(exc)) from exc
            # 在线引擎挂了就退回 Windows 离线语音
            return self._sapi(text)

    # ---------------------------------------------------------------- edge
    def _edge(self, text: str) -> Path:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("没有安装 edge-tts（pip install edge-tts）") from exc

        out = self.dir / f"{uuid.uuid4().hex}.mp3"
        voice = self.config.voice or "zh-CN-XiaoxiaoNeural"
        rate = self.config.rate or "+0%"

        async def run() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=rate)
            await communicate.save(str(out))

        asyncio.run(run())
        if not out.exists() or out.stat().st_size < 1024:
            raise TTSError("edge-tts 没有生成有效文件")
        return out

    # ---------------------------------------------------------------- OpenAI 兼容
    def _openai(self, text: str) -> Path:
        """调用 OpenAI 协议的 /audio/speech；硅基流动、自建 GPT-SoVITS 网关都吃这套。"""
        cfg = self.config
        if not cfg.base_url:
            raise TTSError("openai 引擎需要配置 tts.base_url")
        url = cfg.base_url.rstrip("/") + "/audio/speech"
        payload = {
            "model": cfg.model or "gpt-4o-mini-tts",
            "input": text,
            "voice": cfg.voice or "alloy",
            "response_format": cfg.response_format or "mp3",
        }
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        resp = self._http.post(url, json=payload, headers=headers, timeout=60)
        if resp.status_code != 200:
            raise TTSError(f"TTS 接口返回 HTTP {resp.status_code}: {resp.text[:200]}")
        data = resp.content
        if len(data) < 1024:
            raise TTSError("TTS 接口返回内容过短")

        ext = "wav" if (cfg.response_format or "").lower() == "wav" else "mp3"
        out = self.dir / f"{uuid.uuid4().hex}.{ext}"
        out.write_bytes(data)
        return out

    # ---------------------------------------------------------------- sapi
    def _sapi(self, text: str) -> Path:
        """用 Windows 自带的 System.Speech 合成 wav（离线）。"""
        powershell = shutil.which("powershell") or shutil.which("pwsh")
        if not powershell:
            raise TTSError("找不到 PowerShell，无法使用系统语音")

        out = self.dir / f"{uuid.uuid4().hex}.wav"
        safe_text = text.replace("'", "''").replace("\n", " ")
        script = (
            "Add-Type -AssemblyName System.Speech; "
            "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
            "$cn = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -like 'zh*' } | Select-Object -First 1; "
            "if ($cn) { $s.SelectVoice($cn.VoiceInfo.Name) }; "
            f"$s.SetOutputToWaveFile('{out}'); "
            f"$s.Speak('{safe_text}'); $s.Dispose()"
        )
        result = subprocess.run(
            [powershell, "-NoProfile", "-Command", script],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0 or not out.exists() or out.stat().st_size < 1024:
            raise TTSError(f"系统语音合成失败：{result.stderr[:200]!r}")
        return out

    # ---------------------------------------------------------------- 清理
    def cleanup(self, keep_seconds: float = 600) -> int:
        """删掉十分钟前的临时语音，返回删除数量。"""
        cutoff = time.time() - keep_seconds
        removed = 0
        for path in self.dir.glob("*"):
            if path.suffix.lower() not in (".mp3", ".wav"):
                continue
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed
