"""话题归一与 slug 生成（话题体系 15 §3）。

被 topic_service 与回填脚本共用，保证写路径与迁移路径归一规则一致。
"""

from __future__ import annotations

import re

# 折叠连续空白、trim；归一只影响"展示名"，唯一性判定走 lower(name)（DB 偏函数唯一索引）。
_WS = re.compile(r'\s+')
# slug 仅保留 ascii 字母数字与连字符；中文等非 ascii 名落到 fallback。
_SLUG_KEEP = re.compile(r'[^a-z0-9]+')


def normalize_topic_name(raw: str) -> str:
    """trim + 折叠内部空白；返回用于展示与唯一比较的归一名。"""
    return _WS.sub(' ', (raw or '').strip())


def slugify_topic(name: str, fallback_token: str) -> str:
    """生成 URL 友好 slug。

    - ascii 名：小写、空白/符号折叠为单连字符、trim 连字符；
    - 纯中文 / slug 为空：回退到 `t-{fallback_token}`（保证非空、稳定唯一来源由调用方保证）。
    """
    base = _SLUG_KEEP.sub('-', (name or '').lower()).strip('-')
    if not base:
        return f't-{fallback_token}'
    return base[:80]
