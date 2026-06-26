"""URL 规范化纯函数单元测试（零 mock，无 DB/网络）。

事实源: docs/AI自动获客任务系统/08-采集引擎v3选型决策与众包线索池架构.md §4.4。
"""

from __future__ import annotations

from backend.app.hasn_growth.service.url_normalize import normalize_url


class TestNormalizeUrl:
    def test_lowercases_host_and_strips_www(self) -> None:
        normalized, url_hash, domain = normalize_url('http://www.Example.COM/Path')
        assert normalized == 'http://example.com/Path'  # host 小写去 www；path 大小写保留
        assert domain == 'example.com'
        assert len(url_hash) == 64

    def test_adds_scheme_when_missing(self) -> None:
        normalized, _, domain = normalize_url('example.com/contact')
        assert normalized == 'https://example.com/contact'
        assert domain == 'example.com'

    def test_root_path_and_trailing_slash_unify(self) -> None:
        # example.com / example.com/ / example.com（带根斜杠）规范化为同一
        a = normalize_url('https://example.com')[0]
        b = normalize_url('https://example.com/')[0]
        assert a == b == 'https://example.com'

    def test_trailing_slash_stripped_on_subpath(self) -> None:
        assert normalize_url('https://example.com/a/b/')[0] == 'https://example.com/a/b'

    def test_drops_fragment(self) -> None:
        assert normalize_url('https://example.com/a#section')[0] == 'https://example.com/a'

    def test_drops_tracking_params_keeps_real(self) -> None:
        normalized = normalize_url('https://example.com/p?utm_source=x&id=5&fbclid=abc&gclid=z')[0]
        assert normalized == 'https://example.com/p?id=5'  # 仅保留业务参数 id

    def test_sorts_query_params(self) -> None:
        # 参数顺序不同的同一资源规范化后一致
        a = normalize_url('https://example.com/p?b=2&a=1')[0]
        b = normalize_url('https://example.com/p?a=1&b=2')[0]
        assert a == b

    def test_same_resource_same_hash(self) -> None:
        h1 = normalize_url('http://WWW.example.com/Page/?utm_source=ad')[1]
        h2 = normalize_url('http://example.com/Page')[1]
        assert h1 == h2

    def test_different_resource_different_hash(self) -> None:
        assert normalize_url('https://example.com/a')[1] != normalize_url('https://example.com/b')[1]

    def test_strips_port_in_domain(self) -> None:
        _, _, domain = normalize_url('https://example.com:8080/a')
        assert domain == 'example.com'

    def test_invalid_returns_none(self) -> None:
        assert normalize_url('') is None
        assert normalize_url(None) is None
        assert normalize_url('   ') is None
