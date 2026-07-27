from typing import Any, cast

from sqlalchemy.engine import CursorResult


def affected_rows(result: Any) -> int:
    """读取 SQLAlchemy DML 结果影响行数，并把驱动返回的空值归一为零。"""
    return int(cast(CursorResult[Any], result).rowcount or 0)
