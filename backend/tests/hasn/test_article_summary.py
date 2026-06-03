"""文章摘要兜底（从正文自动提取）单测。"""

from __future__ import annotations

from backend.app.hasn_community.service.article_summary import effective_summary, excerpt_from_content


def test_effective_summary_prefers_explicit() -> None:
    assert effective_summary('手填摘要', '正文很长很长……') == '手填摘要'


def test_effective_summary_falls_back_to_content() -> None:
    assert effective_summary('', 'Hello world from body') == 'Hello world from body'
    assert effective_summary('   ', 'body text') == 'body text'
    assert effective_summary(None, 'body text') == 'body text'


def test_excerpt_strips_markdown() -> None:
    md = '# 标题\n\n这是**正文**，含 `code` 和 [链接](http://x)。'
    out = excerpt_from_content(md)
    assert '#' not in out and '*' not in out and '`' not in out
    assert '标题' in out and '正文' in out and '链接' in out and 'http://x' not in out


def test_excerpt_truncates_with_ellipsis() -> None:
    out = excerpt_from_content('啊' * 300, max_len=120)
    assert len(out) <= 121 and out.endswith('…')


def test_empty_content_returns_empty() -> None:
    assert effective_summary('', '') == ''
    assert effective_summary(None, None) == ''
