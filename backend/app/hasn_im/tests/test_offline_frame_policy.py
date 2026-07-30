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


@pytest.mark.parametrize(
    'method',
    (
        'hasn.message.invalidated',
        'hasn.conversation.invalidated',
        'hasn.task.exec',
    ),
)
def test_critical_invalidations_and_task_frames_have_durable_recovery(method: str) -> None:
    """增量 sync 已在 daemon 事务落地，关键帧不再依赖 Redis offline。"""
    assert OFFLINE_FRAME_POLICIES[method].category is OfflineFrameCategory.DURABLE_SYNC


def test_workspace_switched_is_unconsumed_transient_compatibility_notice() -> None:
    """daemon 不消费历史 WorkspaceSwitched 帧，工作台读面始终回源权威接口。"""
    assert OFFLINE_FRAME_POLICIES['WorkspaceSwitched'].category is OfflineFrameCategory.TRANSIENT


@pytest.mark.parametrize('mode', ('redis', 'dual'))
def test_redis_and_dual_keep_durable_shadow_frame_during_migration(mode: str) -> None:
    """redis/dual 继续保留持久帧加速副本，供迁移期真实对账。"""
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


@pytest.mark.parametrize(
    'method,params',
    (
        ('hasn.message.new', {'message_id': 'msg-1'}),
        (
            'hasn.message.invalidated',
            {'event_id': 'evt-1', 'message_id': 'msg-1'},
        ),
        (
            'hasn.conversation.invalidated',
            {'event_id': 'evt-2', 'conversation_id': 'conv-1'},
        ),
        ('hasn.task.exec', {'dispatch_id': 'task:run:1:exec'}),
    ),
)
def test_sync_mode_skips_all_durable_frames(method: str, params: dict) -> None:
    """全部关键帧具备持久恢复后，sync 模式必须停止写 Redis offline。"""
    payload = json.dumps({
        'hasn': 'hasn/0.2',
        'method': method,
        'params': params,
    })
    assert decide_offline_storage(payload, 'sync') is OfflineStorageAction.SKIP


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
