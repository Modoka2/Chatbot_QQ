from __future__ import annotations

import pytest

from bot.handler import ChatHandler
from bot.models import Message
from bot.search import SearchClient, SearchResult, _extract_so
from tests.conftest import FakeLLM, build_config

SO_HTML = """
<html><body><ul class="result">
<li class="res-list" data-result-type="normal"><h3 class="res-title " >
<a data-mdurl="https://www.sina.cn/news/detail/1.html" href="https://www.so.com/link?m=x"><em>DeepSeek V4</em> 即将发布_新浪新闻</a>
</h3><p class="res-desc">2026年7月21日 - 正式版即将发布，性能接近 Opus 4.8。</p></li>
<li class="res-list" data-result-type="normal"><h3 class="res-title " >
<a data-mdurl="https://www.toutiao.com/article/2/" href="https://www.so.com/link?m=y">DeepSeek-V4 出来了 - 今日头条</a>
</h3><p class="res-desc">上下文窗口提升到 1M。</p></li>
<li class="res-list"><h3 class="res-title">DeepSeek V4 出来了 - 今日头条</h3></li>
</ul></body></html>
"""


def msg(text: str, **kwargs) -> Message:
    data = {
        "platform": "onebot11",
        "chat_type": "private",
        "chat_id": "10001",
        "user_id": "10001",
        "text": text,
    }
    data.update(kwargs)
    return Message(**data)


class FakeSearch:
    def __init__(self, results=None):
        self.calls = []
        self.results = results if results is not None else [
            SearchResult("北京今天多云", "https://weather.example.com/1", "多云转晴，12~20℃"),
        ]

    def search(self, query, limit=None):
        self.calls.append(query)
        return self.results


# ------------------------------------------------------------------ 解析
def test_extract_so():
    results = _extract_so(SO_HTML, 5)
    assert len(results) == 2  # 第三条没链接，去重后丢掉
    assert results[0].url == "https://www.sina.cn/news/detail/1.html"
    assert "DeepSeek" in results[0].title
    assert "Opus" in results[0].snippet


def test_extract_so_duplicate_title_removed():
    results = _extract_so(SO_HTML, 5)
    titles = [r.title for r in results]
    assert len(titles) == len(set(titles))


def test_extract_so_detects_captcha():
    from bot.search import SearchError

    with pytest.raises(SearchError):
        _extract_so("<html>百度安全验证</html>", 5)


# ------------------------------------------------------------------ 客户端
class _Resp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return {}


class _Session:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_client_falls_back_to_second_provider():
    cfg = build_config(search={"enabled": True, "provider": "auto"})
    session = _Session([_Resp(text="<html>验证码</html>"), _Resp(text=SO_HTML)])
    client = SearchClient(cfg.search, session=session)
    results = client.search("DeepSeek V4")
    assert len(results) == 2
    assert len(session.calls) == 2


def test_client_returns_empty_when_all_fail():
    cfg = build_config(search={"enabled": True, "provider": "auto"})
    session = _Session([_Resp(status=500), _Resp(status=500)])
    client = SearchClient(cfg.search, session=session)
    assert client.search("随便") == []


def test_client_rejects_short_query():
    cfg = build_config(search={"enabled": True})
    client = SearchClient(cfg.search, session=_Session([]))
    assert client.search("a") == []


# ------------------------------------------------------------------ 接入聊天
def test_auto_search_injects_context(handler):
    handler.config.search.enabled = True
    handler.config.search.auto_keywords = ["搜一下"]
    handler._search_client = FakeSearch()

    handler.handle(msg("搜一下 北京今天天气"))

    content = handler.llm.calls[-1][-1]["content"]
    assert "[联网搜索" in content
    assert "北京今天天气" in content
    assert "https://weather.example.com/1" in content


def test_auto_search_disabled(handler):
    handler.config.search.enabled = False
    handler._search_client = FakeSearch()
    handler.handle(msg("搜一下 北京天气"))
    assert "[联网搜索" not in handler.llm.calls[-1][-1]["content"]
    assert handler._search_client.calls == []


def test_search_command(handler):
    handler.config.search.enabled = True
    handler._search_client = FakeSearch()
    reply = handler.handle(msg("/搜索 北京天气"))
    assert reply == "好的呀"
    assert handler._search_client.calls == ["北京天气"]
    assert "https://weather.example.com/1" in handler.llm.calls[-1][-1]["content"]


def test_search_command_without_results(handler):
    handler.config.search.enabled = True
    handler._search_client = FakeSearch(results=[])
    reply = handler.handle(msg("/搜索 找不到的东西"))
    assert "没搜到" in reply


def test_search_command_disabled(handler):
    handler.config.search.enabled = False
    handler._search_client = FakeSearch()
    reply = handler.handle(msg("/搜索 北京天气"))
    assert "还没开" in reply


def test_search_failure_does_not_break_chat(handler):
    handler.config.search.enabled = True
    handler.config.search.auto_keywords = ["搜一下"]

    class Boom:
        def search(self, query, limit=None):
            raise RuntimeError("搜索挂了")

    handler._search_client = Boom()
    assert handler.handle(msg("搜一下 北京天气")) == "好的呀"


# ------------------------------------------------------------------ 查询改写
def test_fresh_query_rewrites_release_question(handler):
    q = handler._fresh_query(handler._clean_query("搜一下 DeepSeek V4 什么时候发布"))
    assert q == "DeepSeek V4 发布 最新进展"


def test_fresh_query_keeps_plain_question(handler):
    assert handler._fresh_query("北京 天气") == "北京 天气"


def test_fresh_query_keeps_already_fresh(handler):
    assert handler._fresh_query("DeepSeek V4 最新进展") == "DeepSeek V4 最新进展"
