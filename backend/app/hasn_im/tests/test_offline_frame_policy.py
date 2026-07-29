"""方案 B 离线帧 durable 覆盖策略测试。"""

from __future__ import annotations

import json

import pytest

from backend.app.hasn_im.adapters.routing.offline_frame_policy import (
    OFFLINE_FRAME_POLICIES,
    OfflineFrameCategory,
    OfflineFramePolicyError,
    OfflineStorageAction,
    classify_offline_frame,
    decide_offline_storage,
    require_registered_offline_method,
)


def test_offline_frame_registry_covers_current_production_methods() -> None:
    """当前所有可进入离线路径的生产帧必须显式登记。"""
    assert set(OFFLINE_FRAME_POLICIES) == {
        'WorkspaceSwitched',
        'hasn.contact.connected',
        'hasn.contact.removed',
        'hasn.contact.request_received',
        'hasn.conversation.invalidated',
        'hasn.message.invalidated',
        'hasn.message.new',
        'hasn.task.exec',
        'hasn.typing',
    }


@pytest.mark.parametrize(
    ('method', 'params', 'expected_identity'),
    (
        ('hasn.message.new', {'message_id': 'msg-1'}, 'msg-1'),
        (
            'hasn.message.invalidated',
            {'event_id': 'evt-1', 'message_id': 'msg-1'},
            'evt-1',
        ),
        (
            'hasn.conversation.invalidated',
            {'event_id': 'evt-2', 'conversation_id': 'conv-1'},
            'evt-2',
        ),
        ('hasn.task.exec', {'dispatch_id': 'task:run:1:exec'}, 'task:run:1:exec'),
        ('hasn.contact.request_received', {'request_id': 7}, '7'),
        ('hasn.contact.connected', {'request_id': 8}, '8'),
        ('hasn.contact.removed', {'peer_id': 'h-peer'}, 'h-peer'),
    ),
)
def test_offline_frame_uses_stable_identity(
    method: str,
    params: dict,
    expected_identity: str,
) -> None:
    """需要离线恢复或对账的帧必须能提取稳定身份。"""
    decision = classify_offline_frame(json.dumps({'hasn': 'hasn/0.2', 'method': method, 'params': params}))
    assert decision.method == method
    assert decision.identity == expected_identity


def test_typing_is_transient_and_never_requires_offline_storage() -> None:
    """输入中状态是在线瞬时信号，不得伪装成离线消息。"""
    decision = classify_offline_frame(
        json.dumps({
            'hasn': 'hasn/0.2',
            'method': 'hasn.typing',
            'params': {'conversation_id': 'conv-1'},
        })
    )
    assert decision.category is OfflineFrameCategory.TRANSIENT
    assert decision.identity is None


def test_task_exec_remains_a_gap_until_durable_command_recovery_exists() -> None:
    """业务 outbox 仅保证到实时端口，尚不能证明 daemon 离线后可恢复执行命令。"""
    assert OFFLINE_FRAME_POLICIES['hasn.task.exec'].category is OfflineFrameCategory.GAP


@pytest.mark.parametrize('mode', ('redis', 'dual'))
def test_redis_and_dual_keep_gap_frames_during_migration(mode: str) -> None:
    """缺口补齐前，redis/dual 继续保留既有加速副本。"""
    payload = json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.task.exec',
        'params': {'dispatch_id': 'task:run:1:exec'},
    })
    assert decide_offline_storage(payload, mode) is OfflineStorageAction.STORE


@pytest.mark.parametrize('mode', ('redis', 'dual', 'sync'))
def test_transient_frame_is_never_written_offline(mode: str) -> None:
    """瞬时帧在所有恢复模式都不得写入 Redis offline。"""
    payload = json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.typing',
        'params': {'conversation_id': 'conv-1'},
    })
    assert decide_offline_storage(payload, mode) is OfflineStorageAction.SKIP


def test_sync_mode_skips_durable_frame_and_rejects_gap() -> None:
    """sync 只允许已证明可恢复的帧停写 Redis，缺口必须阻断切换。"""
    durable = json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.new',
        'params': {'message_id': 'msg-1'},
    })
    assert decide_offline_storage(durable, 'sync') is OfflineStorageAction.SKIP

    gap = json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.task.exec',
        'params': {'dispatch_id': 'task:run:1:exec'},
    })
    with pytest.raises(OfflineFramePolicyError, match='尚有 durable 缺口'):
        decide_offline_storage(gap, 'sync')


def test_unknown_offline_recovery_mode_fails_explicitly() -> None:
    """绕过配置类型系统传入非法模式时也必须失败。"""
    payload = json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.new',
        'params': {'message_id': 'msg-1'},
    })
    with pytest.raises(OfflineFramePolicyError, match='恢复模式非法'):
        decide_offline_storage(payload, 'unknown')


def test_realtime_gateway_rejects_unregistered_owner_method() -> None:
    """新增 owner 实时帧源必须先进入覆盖矩阵。"""
    assert require_registered_offline_method('hasn.message.new').method == 'hasn.message.new'
    with pytest.raises(OfflineFramePolicyError, match='方法未登记'):
        require_registered_offline_method('hasn.new_source')


@pytest.mark.parametrize(
    'payload',
    (
        'not-json',
        '[]',
        '{"hasn":"hasn/0.2","params":{}}',
        '{"hasn":"hasn/0.2","method":"hasn.unknown","params":{}}',
        '{"hasn":"hasn/0.2","method":"hasn.message.new","params":{}}',
    ),
)
def test_unregistered_or_unidentifiable_frame_fails_explicitly(payload: str) -> None:
    """未知来源或关键 ID 缺失必须显式报错，禁止静默进入未审计队列。"""
    with pytest.raises(OfflineFramePolicyError):
        classify_offline_frame(payload)
