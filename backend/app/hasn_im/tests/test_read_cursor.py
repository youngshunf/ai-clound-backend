"""hasn_im.domain.read_cursor 纯模块单测（R1-10「read cursor 单调」·无 DB 恒跑）

钉死已读游标只进不退不变量（§4.3）：新建行、正常推进、竞态/陈旧低值不倒退、None/负值归一。
"""

from __future__ import annotations

from backend.app.hasn_im.domain.read_cursor import monotonic_read_cursor


def test_new_row_takes_incoming() -> None:
    # 新建 unread 行（existing=None）：采纳 incoming。
    assert monotonic_read_cursor(None, 42) == 42


def test_new_row_incoming_zero_or_none_stays_zero() -> None:
    # 新建行且不指定目标（0/None）：游标停在 0 基线。
    assert monotonic_read_cursor(None, 0) == 0
    assert monotonic_read_cursor(None, None) == 0


def test_advances_forward() -> None:
    # 正常场景：incoming > existing → 推进到 incoming。
    assert monotonic_read_cursor(5, 9) == 9


def test_does_not_regress_on_stale_lower_incoming() -> None:
    # 核心 bug 纠正：陈旧/乱序低值请求绝不把游标拨回去。
    assert monotonic_read_cursor(9, 3) == 9
    assert monotonic_read_cursor(100, 0) == 100
    assert monotonic_read_cursor(100, None) == 100


def test_equal_is_idempotent() -> None:
    assert monotonic_read_cursor(7, 7) == 7


def test_negative_values_normalized_to_zero_baseline() -> None:
    # 游标从不为负：负 existing / 负 incoming 一律按 0 处理。
    assert monotonic_read_cursor(-1, -5) == 0
    assert monotonic_read_cursor(-1, 4) == 4
    assert monotonic_read_cursor(6, -5) == 6


def test_monotone_never_below_existing() -> None:
    # 通用性质：返回值恒 >= 归一后的 existing（只进不退）。
    for existing in (None, 0, 3, 50):
        for incoming in (None, 0, 1, 3, 49, 51):
            base = existing if (existing is not None and existing > 0) else 0
            assert monotonic_read_cursor(existing, incoming) >= base
