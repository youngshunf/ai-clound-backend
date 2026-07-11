"""doc08 RT1.5·§4.1：拦截箱列表补「关联好友请求 + 来源主人」字段（`list_suppressed_for_owner`）。

不依赖真实 PG：纯逻辑覆盖 `_build_suppressed_item`（把一行抑制记录 + 预解析的发送方主人映射拼成
对 daemon 的 item），断言 RT1.5 新增字段 `sender_hasn_id` / `sender_owner_hasn_id` /
`sender_owner_name` / `pending_request_id` 的落点与「无值留 null」诚实回落语义。
"""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.hasn.service.inbound_release import _build_suppressed_item


def _row(**overrides):
    """构造一行 RowMapping 风格的抑制记录（dict 即可，`_build_suppressed_item` 用 [] 与 .get()）。"""
    base = {
        'suppressed_id': 42,
        'message_id': 1001,
        'conversation_id': 'c_conv_1',
        'hasn_id': 'a_my_agent',  # 接收方分身（我的分身）
        'suppress_reason': 'permission_denied',
        'created_time': datetime(2026, 7, 8, tzinfo=timezone.utc),
        'from_id': 'a_peer_agent',  # 发送方分身（来源）
        'policy_snapshot': {},
        'content': {'text': '你好，想加个好友'},
    }
    base.update(overrides)
    return base


def test_item_carries_sender_owner_and_pending_request() -> None:
    """发送分身有主人 + snapshot 带 pending_request_id → 三组新字段齐备。"""
    row = _row(policy_snapshot={'pending_request_id': 555})
    sender_owners = {'a_peer_agent': ('h_owner_b', '福仔')}

    item = _build_suppressed_item(row, sender_owners)

    assert item['sender_hasn_id'] == 'a_peer_agent'
    assert item['sender_owner_hasn_id'] == 'h_owner_b'
    assert item['sender_owner_name'] == '福仔'
    assert item['pending_request_id'] == '555'  # 统一字符串化
    # 既有字段不受影响
    assert item['agent_hasn_id'] == 'a_my_agent'
    assert item['message_id'] == '1001'
    assert item['reason'] == 'permission_denied'
    assert item['message_preview'] == '你好，想加个好友'


def test_item_null_when_remote_sender_unresolvable() -> None:
    """远端 / 无从解析的发送方 → 来源主人字段诚实留 null（零 fake），仍带 sender_hasn_id。"""
    row = _row(from_id='a_remote_agent')
    item = _build_suppressed_item(row, sender_owners={})

    assert item['sender_hasn_id'] == 'a_remote_agent'
    assert item['sender_owner_hasn_id'] is None
    assert item['sender_owner_name'] is None
    assert item['pending_request_id'] is None


def test_item_no_pending_request_when_snapshot_empty() -> None:
    """snapshot 无 pending_request_id → 该字段 null；主人已解析仍照常带。"""
    row = _row(policy_snapshot={'other': 1})
    item = _build_suppressed_item(row, {'a_peer_agent': ('h_owner_b', None)})

    assert item['pending_request_id'] is None
    assert item['sender_owner_hasn_id'] == 'h_owner_b'
    # 主人昵称缺失（未在 humans 表命中）→ null，但 owner_hasn_id 仍在
    assert item['sender_owner_name'] is None


def test_item_human_sender_has_no_owner() -> None:
    """发送方是人（h_ 前缀）→ 无主人可解析，来源主人留 null，sender_hasn_id 照常回显。"""
    row = _row(from_id='h_external_human')
    item = _build_suppressed_item(row, sender_owners={})

    assert item['sender_hasn_id'] == 'h_external_human'
    assert item['sender_owner_hasn_id'] is None
    assert item['sender_owner_name'] is None
