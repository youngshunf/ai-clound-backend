"""hasn_im.domain.read_cursor · 已读游标单调推进（纯逻辑·R1-10「read cursor 单调」）

16 号 §4.3：read 游标（R1 legacy `last_read_msg_id`、R2 `read_seq`）**只进不退**——
陈旧 / 乱序到达的低值请求绝不能把游标拨回去（否则已读消息被判回未读、未读数虚高）。

本模块只承载这条不变量的**纯函数**（无 IO / 无 Session / 无 Redis / 无 ORM，符合 R1-01
domain 负面约束）：R1 期供 legacy `hasn_im.py::mark_conversation_read` 消费，保形接入（对
合法单调客户端行为逐字节不变，仅纠正竞态/陈旧低值倒退这一 bug）；R2 期 `read_seq` 推进复用
同一不变量（§4.3 read_seq 单调）。
"""

from __future__ import annotations


def _as_nonneg(value: int | None) -> int:
    """把 None / 负值归一为 0 基线（游标从不为负；缺失视作『还未读到任何消息』）。"""
    if value is None or value < 0:
        return 0
    return value


def monotonic_read_cursor(existing: int | None, incoming: int | None) -> int:
    """已读游标单调推进：返回 max(existing, incoming)，绝不回退。

    - `existing`：当前落库的游标（新建行传 None）。
    - `incoming`：本次请求携带的目标游标（缺省 / 0 表示不指定、不推进）。
    - None / 负值一律按 0 基线处理；返回值恒 >= existing（只进不退）。

    与 legacy 行为的差异**仅**在竞态/陈旧场景：incoming < existing 时旧代码会覆写倒退，
    本函数保持 existing 不动——这是 bug 纠正，合法单调客户端不受影响。
    """
    return max(_as_nonneg(existing), _as_nonneg(incoming))
