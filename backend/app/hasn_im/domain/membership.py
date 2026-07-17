"""hasn_im.domain.membership · 成员周期与已读/未读的纯逻辑（R2-03·16 号 §4.2/§4.3）

权威事实只有序号：`message.conversation_seq`、membership 的 `joined_seq/left_seq/read_seq`、
以及消息可见性与 sender 归属。未读数**不是**权威事实，只能由这些序号重算。本模块只承载这条
契约的**纯函数**（无 IO / 无 Session / 无 Redis / 无 ORM，符合 domain 负面约束），供 application
层的 epoch 操作与 unread reconciler 复用。

被否决形态（D8）：重新加入覆盖唯一成员行、丢失历史可见边界；把未读退回可读改写计数事实。
"""

from __future__ import annotations


def is_message_visible_in_epoch(message_seq: int, joined_seq: int, left_seq: int | None) -> bool:
    """某成员周期内消息是否可见（§4.2）：

    ``message.seq >= joined_seq AND (left_seq IS NULL OR message.seq <= left_seq)``

    - 加入前（seq < joined_seq）的历史不可见；
    - 退出后（left_seq 非空且 seq > left_seq）的消息不可见；
    - 活动周期（left_seq IS NULL）只有下界，无上界。
    """
    if message_seq < joined_seq:
        return False
    if left_seq is not None and message_seq > left_seq:
        return False
    return True


def epoch_visible_upper_bound(left_seq: int | None, current_seq: int) -> int:
    """周期可见区间的上界：活动周期到会话当前游标 `current_seq`，已闭合周期到 `left_seq`。

    供 read_seq 推进时 clamp 用——已读游标不能越过本周期能看到的最后一条。
    """
    return current_seq if left_seq is None else left_seq


def advance_read_seq(old_read_seq: int, incoming_seq: int, visible_upper_bound: int) -> int:
    """已读游标推进（§4.3）：``read_seq = max(old_read_seq, min(incoming_seq, visible_upper_bound))``

    - 单调只进不退（与 [read_cursor.monotonic_read_cursor] 同精神，见 domain/read_cursor.py）；
    - clamp 到本周期可见上界，不让已读越过看不见的消息（否则未读永远清不零）。
    """
    capped = min(incoming_seq, visible_upper_bound)
    return max(old_read_seq, capped)


def rejoin_read_seq(joined_seq: int) -> int:
    """重入新周期的初始 read_seq（§4.2）：``read_seq = joined_seq - 1``——

    即『尚未读到本周期任何消息』（本周期第一条消息 seq == joined_seq，仍算未读）。
    """
    return joined_seq - 1


def counts_toward_unread(
    *,
    conversation_seq: int,
    read_seq: int,
    joined_seq: int,
    left_seq: int | None,
    is_visible: bool,
    sender_owner_id: str | None,
    viewer_owner_id: str | None,
) -> bool:
    """单条消息是否计入某 viewer 的未读（§4.3 权威谓词）：

    ``conversation_seq > read_seq AND 位于当前 membership 可见区间 AND message 对该主体可见
    AND sender 不属于该 viewer owner``

    自己发的、不可见、抑制、周期外的消息都不计未读——所以未读绝不能简单等于
    `current_seq - read_seq`。
    """
    if conversation_seq <= read_seq:
        return False
    if not is_message_visible_in_epoch(conversation_seq, joined_seq, left_seq):
        return False
    if not is_visible:
        return False
    if sender_owner_id is not None and viewer_owner_id is not None and sender_owner_id == viewer_owner_id:
        return False
    return True
