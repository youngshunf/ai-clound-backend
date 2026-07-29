"""知识库 Markdown 正文内联资产解析。"""

from __future__ import annotations

import re

from markdown_it import MarkdownIt

_ASSET_URI_RE = re.compile(r'hasn://asset/([A-Za-z0-9_-]+)\Z')
_MARKDOWN = MarkdownIt('commonmark')


def asset_ids_from_content(content: str | None) -> set[str]:
    """只收页面实际渲染出的 Markdown 图片节点，不信任普通文本、链接或原始 HTML。"""
    if not content:
        return set()
    asset_ids: set[str] = set()
    for token in _MARKDOWN.parse(content):
        for child in token.children or ():
            if child.type != 'image':
                continue
            source = child.attrGet('src')
            if not isinstance(source, str):
                continue
            match = _ASSET_URI_RE.fullmatch(source)
            if match:
                asset_ids.add(match.group(1))
    return asset_ids
