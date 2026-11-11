"""图片读取：把 QQ / 微信里的图片变成模型能吃的 data URI。

来源可能有三种：
- https://...        QQ 图片 CDN、公众号 PicUrl
- file:///D:/...     NapCat 把图片存到本地时给的地址
- D:\\path\\a.jpg    直接用本地路径

安全约束：只接受图片、限制大小、限制张数，超时即放弃。
"""

from __future__ import annotations

import base64
import ipaddress
import logging
import mimetypes
import os
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

log = logging.getLogger(__name__)

_IMAGE_MIME = {
    b"\x89PNG\r\n\x1a\n": "image/png",
    b"\xff\xd8\xff": "image/jpeg",
    b"GIF87a": "image/gif",
    b"GIF89a": "image/gif",
    b"BM": "image/bmp",
}
_EXT_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


class ImageError(RuntimeError):
    """图片拿不到 / 不合法。"""


def sniff_mime(data: bytes) -> str | None:
    """按文件头判断是不是图片。"""
    for magic, mime in _IMAGE_MIME.items():
        if data.startswith(magic):
            return mime
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _is_loopback(host: str) -> bool:
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in ("localhost",)


def load_image(ref: str, max_mb: float = 8.0, timeout: float = 20.0) -> tuple[bytes, str]:
    """读取一张图片，返回 (原始字节, mime)。失败抛 :class:`ImageError`。"""
    ref = str(ref or "").strip()
    limit = int(max_mb * 1024 * 1024)

    if not ref:
        raise ImageError("空地址")

    if ref.startswith(("http://", "https://")):
        data = _fetch_http(ref, limit, timeout)
    elif ref.startswith("file://"):
        path = unquote(urlparse(ref).path)
        if os.name == "nt" and path.startswith("/"):
            path = path.lstrip("/")
        data = _read_file(path, limit)
    elif os.path.isabs(ref) or Path(ref).exists():
        data = _read_file(ref, limit)
    else:
        raise ImageError(f"不支持的图片地址：{ref[:60]}")

    mime = sniff_mime(data)
    if not mime:
        raise ImageError("下载到的不是图片")
    return data, mime


def to_data_uri(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def _fetch_http(url: str, limit: int, timeout: float) -> bytes:
    host = urlparse(url).hostname or ""
    if host and _is_loopback(host):
        log.debug("图片来自本机地址（NapCat 本地文件服务）：%s", host)
    try:
        resp = requests.get(url, timeout=timeout, stream=True)
    except requests.RequestException as exc:
        raise ImageError(f"下载失败：{exc}") from exc

    if resp.status_code != 200:
        raise ImageError(f"下载失败：HTTP {resp.status_code}")

    ctype = (resp.headers.get("Content-Type") or "").lower()
    if ctype and not ctype.startswith("image/"):
        raise ImageError(f"不是图片：{ctype}")

    declared = resp.headers.get("Content-Length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise ImageError(f"图片太大：{int(declared) / 1024 / 1024:.1f} MB")

    buf = bytearray()
    for chunk in resp.iter_content(64 * 1024):
        buf.extend(chunk)
        if len(buf) > limit:
            raise ImageError(f"图片超过 {limit / 1024 / 1024:.0f} MB")
    return bytes(buf)


def _read_file(path: str, limit: int) -> bytes:
    p = Path(path)
    if not p.is_file():
        raise ImageError(f"文件不存在：{path}")
    if p.suffix.lower() not in _EXT_MIME:
        raise ImageError(f"不是图片后缀：{p.suffix}")
    size = p.stat().st_size
    if size > limit:
        raise ImageError(f"图片太大：{size / 1024 / 1024:.1f} MB")
    return p.read_bytes()


def guess_mime(ref: str) -> str:
    return _EXT_MIME.get(Path(urlparse(ref).path).suffix.lower(), mimetypes.guess_type(ref)[0] or "image/jpeg")
