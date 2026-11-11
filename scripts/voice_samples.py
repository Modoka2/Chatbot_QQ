"""生成音色试听包：把可用的中文音色各合成一句话，附一个 index.html。

用法：
    python scripts/voice_samples.py                 # 默认试听文本
    python scripts/voice_samples.py "自定义一句话"

生成到 data/voice_samples/，双击 index.html 就能挨个听。
挑好之后把 config.local.yaml 里的 tts.voice 改成对应名字，重启机器人生效。
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DEFAULT_TEXT = "主人好呀，我是小莫，这个音色你喜欢吗？"

# 常见中文音色的人工备注（没列到的会显示成「其他」）
NOTES = {
    "zh-CN-XiaoxiaoNeural": "女声，温柔、自然，最像日常聊天（当前默认）",
    "zh-CN-XiaoyiNeural": "女声，更年轻、更俏皮，适合活泼人设",
    "zh-CN-YunxiNeural": "男声，清亮、有少年感",
    "zh-CN-YunjianNeural": "男声，低沉，偏解说/体育风",
    "zh-CN-YunyangNeural": "男声，播音腔，适合新闻播报",
    "zh-CN-YunxiaNeural": "男声，少年音，偏稚嫩",
    "zh-CN-liaoning-XiaobeiNeural": "女声，东北腔，很有梗",
    "zh-CN-shaanxi-XiaoniNeural": "女声，陕西腔",
    "zh-TW-HsiaoChenNeural": "女声，台湾腔，软糯",
    "zh-TW-YunJheNeural": "男声，台湾腔",
    "zh-HK-HiuMaanNeural": "女声，粤语",
    "zh-HK-WanLungNeural": "男声，粤语",
}


def pick_voices() -> list[dict]:
    import edge_tts

    voices = asyncio.run(edge_tts.list_voices())
    zh = [v for v in voices if str(v.get("Locale", "")).lower().startswith("zh")]
    zh.sort(key=lambda v: (v.get("Locale", ""), v.get("ShortName", "")))
    return zh


def main() -> int:
    text = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_TEXT
    try:
        import edge_tts
    except ImportError:
        print("没装 edge-tts：pip install edge-tts", file=sys.stderr)
        return 1

    voices = pick_voices()
    out_dir = ROOT / "data" / "voice_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("*.mp3"):
        old.unlink()

    print(f"试听文本：{text}\n")
    items = []
    for voice in voices:
        short = voice.get("ShortName", "")
        filename = short.replace(":", "_") + ".mp3"
        target = out_dir / filename

        async def run(name=short, path=target):
            await edge_tts.Communicate(text, name).save(str(path))

        try:
            asyncio.run(run())
        except Exception as exc:
            print(f"  ✗ {short}: {exc}")
            continue

        note = NOTES.get(short, "其他中文音色")
        gender = {"Female": "女", "Male": "男"}.get(voice.get("Gender", ""), "?")
        items.append((short, gender, voice.get("Locale", ""), note, filename))
        print(f"  ✓ {short}  ({gender}/{voice.get('Locale')})  {note}")

    rows = "\n".join(
        f'<li><b>{short}</b> <span class="meta">{gender} · {locale}</span>'
        f'<div class="note">{note}</div>'
        f'<audio controls src="{filename}"></audio></li>'
        for short, gender, locale, note, filename in items
    )
    html = f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>音色试听</title>
<style>
 body{{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:760px;margin:32px auto;padding:0 16px;color:#222}}
 h1{{font-size:20px}} li{{margin:16px 0;list-style:none;padding:12px;border:1px solid #eee;border-radius:10px}}
 .meta{{color:#888;font-size:13px;margin-left:8px}} .note{{color:#555;font-size:14px;margin:6px 0}}
 audio{{width:100%}} code{{background:#f3f4f6;padding:2px 6px;border-radius:4px}}
</style></head><body>
<h1>音色试听</h1>
<p>试听文本：{text}</p>
<p>挑好后改 <code>config.local.yaml</code> 的 <code>tts.voice</code>，例如：
<code>voice: zh-CN-XiaoyiNeural</code>，然后重启机器人。</p>
<ul>{rows}</ul>
</body></html>"""
    (out_dir / "index.html").write_text(html, encoding="utf-8")

    print(f"\n共 {len(items)} 个音色，试听包：{out_dir / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
