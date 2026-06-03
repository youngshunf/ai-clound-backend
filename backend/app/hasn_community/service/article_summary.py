"""文章摘要工具。

两层策略：
- 展示兜底（同步、零成本）：摘要为空时从正文抽纯文本摘要 `effective_summary`，
  让列表/详情立即有摘要可读（"从正文自动提取"）。
- LLM 异步（保存后台任务）：见 community_service 的摘要后台任务，质量更高，
  完成后回写 hasn_articles.summary；未完成/失败时仍由本兜底覆盖展示。
"""

from __future__ import annotations

import re

# 轻量去 Markdown：保留可读文本，去结构符号。顺序敏感（先块后内联）。
_MD_SUBS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r'```.*?```', re.S), ' '),  # 围栏代码块
    (re.compile(r'`([^`]*)`'), r'\1'),  # 行内代码
    (re.compile(r'!\[[^\]]*\]\([^)]*\)'), ' '),  # 图片
    (re.compile(r'\[([^\]]*)\]\([^)]*\)'), r'\1'),  # 链接保留锚文本
    (re.compile(r'^[ \t]*[#>\-\*\+]+[ \t]*', re.M), ''),  # 行首标题/引用/列表标记
    (re.compile(r'[*_~`#>]'), ''),  # 残留强调/标记
]

DEFAULT_EXCERPT_LEN = 120


def excerpt_from_content(content: str | None, *, max_len: int = DEFAULT_EXCERPT_LEN) -> str:
    """从正文抽纯文本摘要：去 Markdown、折叠空白、按字数截断加省略号。"""
    if not content:
        return ''
    text = content
    for pattern, repl in _MD_SUBS:
        text = pattern.sub(repl, text)
    text = re.sub(r'\s+', ' ', text).strip()
    if len(text) <= max_len:
        return text
    return text[:max_len].rstrip() + '…'


def effective_summary(summary: str | None, content: str | None, *, max_len: int = DEFAULT_EXCERPT_LEN) -> str:
    """有摘要用摘要，无摘要从正文抽（展示兜底）。"""
    cleaned = (summary or '').strip()
    if cleaned:
        return cleaned
    return excerpt_from_content(content, max_len=max_len)
