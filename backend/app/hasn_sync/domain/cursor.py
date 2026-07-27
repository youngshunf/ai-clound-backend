"""owner revision 游标的纯解析与校验。"""

from __future__ import annotations


class CursorError(ValueError):
    """同步游标格式或 owner 归属错误。"""


def parse_owner_cursor(cursor: str | None, *, owner_id: str) -> int:
    """解析 ``owner:{owner_id}:{revision}``，并拒绝跨 owner 复用。"""
    if cursor is None or not cursor.strip():
        return 0
    prefix = f'owner:{owner_id}:'
    if not cursor.startswith(prefix):
        raise CursorError('cursor owner 与请求 owner 不一致')
    raw_revision = cursor[len(prefix) :]
    try:
        revision = int(raw_revision)
    except ValueError as exc:
        raise CursorError('cursor revision 不是整数') from exc
    if revision < 0:
        raise CursorError('cursor revision 不能为负数')
    return revision


def owner_cursor(owner_id: str, revision: int) -> str:
    """编码 owner revision 游标。"""
    if revision < 0:
        raise CursorError('cursor revision 不能为负数')
    return f'owner:{owner_id}:{revision}'
