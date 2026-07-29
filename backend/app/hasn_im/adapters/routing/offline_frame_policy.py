"""方案 B 离线帧 durable 覆盖策略。

离线队列只能接收本注册表中已经审计的协议帧。分类语义：

- ``durable_sync``：PostgreSQL sync event 或权威快照能恢复；
- ``transient``：仅在线有意义，不应写离线队列；
- ``gap``：尚无完整 durable 恢复链，切到 ``sync`` 前必须补齐。
"""

from __future__ import annotations

import json

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class OfflineFrameCategory(StrEnum):
    """离线帧的 durable 恢复分类。"""

    DURABLE_SYNC = 'durable_sync'
    TRANSIENT = 'transient'
    GAP = 'gap'


class OfflineStorageAction(StrEnum):
    """一条离线候选帧对 Redis offline 的动作。"""

    STORE = 'store'
    SKIP = 'skip'


@dataclass(frozen=True, slots=True)
class OfflineFramePolicy:
    """一种协议帧的恢复事实与对账身份契约。"""

    method: str
    category: OfflineFrameCategory
    identity_fields: tuple[str, ...]
    durable_source: str
    recovery_path: str


@dataclass(frozen=True, slots=True)
class OfflineFrameDecision:
    """对一条具体帧完成校验后的离线策略结论。"""

    method: str
    category: OfflineFrameCategory
    identity: str | None
    policy: OfflineFramePolicy


class OfflineFramePolicyError(ValueError):
    """离线帧未登记、结构非法或缺少稳定身份。"""


_POLICIES = (
    OfflineFramePolicy(
        method='hasn.message.new',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('message_id',),
        durable_source='PostgreSQL hasn_messages + hasn_sync_events(message.new)',
        recovery_path='daemon sync pull；retention gap 后进入消息历史快照',
    ),
    OfflineFramePolicy(
        method='hasn.message.invalidated',
        category=OfflineFrameCategory.GAP,
        identity_fields=('event_id', 'message_id'),
        durable_source='PostgreSQL integration event + hasn_sync_events(message.recalled)',
        recovery_path='历史快照返回最终状态；daemon 增量 sync 尚未应用 message.recalled',
    ),
    OfflineFramePolicy(
        method='hasn.conversation.invalidated',
        category=OfflineFrameCategory.GAP,
        identity_fields=('event_id', 'conversation_id'),
        durable_source='PostgreSQL integration event + hasn_sync_events(conversation.updated)',
        recovery_path='会话快照提供最终状态；daemon 增量 sync 尚未应用 conversation.updated',
    ),
    OfflineFramePolicy(
        method='hasn.task.exec',
        category=OfflineFrameCategory.GAP,
        identity_fields=('dispatch_id',),
        durable_source='PostgreSQL hasn_task.task_dispatch_outbox',
        recovery_path='当前 relay 在实时端口返回后即完成，daemon 离线超过 Redis TTL 尚不能恢复',
    ),
    OfflineFramePolicy(
        method='hasn.contact.request_received',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('request_id',),
        durable_source='PostgreSQL hasn_contact_requests',
        recovery_path='daemon 联系人请求权威快照刷新',
    ),
    OfflineFramePolicy(
        method='hasn.contact.connected',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('request_id',),
        durable_source='PostgreSQL hasn_contacts + hasn_contact_requests',
        recovery_path='daemon 联系人权威快照刷新',
    ),
    OfflineFramePolicy(
        method='hasn.contact.removed',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('peer_id',),
        durable_source='PostgreSQL hasn_contacts 权威关系状态',
        recovery_path='daemon 联系人权威快照删除缺失关系',
    ),
    OfflineFramePolicy(
        method='WorkspaceSwitched',
        category=OfflineFrameCategory.GAP,
        identity_fields=(),
        durable_source='PostgreSQL owner 工作台 active_enterprise_id',
        recovery_path='已有权威快照，但尚未证明 daemon 重连会主动刷新并失效本地状态',
    ),
    OfflineFramePolicy(
        method='hasn.typing',
        category=OfflineFrameCategory.TRANSIENT,
        identity_fields=(),
        durable_source='无；输入中状态不是业务事实',
        recovery_path='不恢复、不写离线队列',
    ),
)

OFFLINE_FRAME_POLICIES: Mapping[str, OfflineFramePolicy] = MappingProxyType({
    policy.method: policy for policy in _POLICIES
})


def require_registered_offline_method(method: str) -> OfflineFramePolicy:
    """确认 owner 实时方法已经进入 durable 覆盖矩阵。"""
    policy = OFFLINE_FRAME_POLICIES.get(method)
    if policy is None:
        raise OfflineFramePolicyError(f'离线帧方法未登记：{method}')
    return policy


def _stable_identity(params: Mapping[str, Any], fields: tuple[str, ...]) -> str | None:
    for field in fields:
        value = params.get(field)
        if isinstance(value, bool) or value is None:
            continue
        if isinstance(value, (str, int)):
            normalized = str(value).strip()
            if normalized:
                return normalized
    return None


def classify_offline_frame(payload_json: str) -> OfflineFrameDecision:
    """解析并校验一条可能进入离线路径的 HASN 帧。"""
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError) as exc:
        raise OfflineFramePolicyError('离线帧不是合法 JSON') from exc
    if not isinstance(payload, dict):
        raise OfflineFramePolicyError('离线帧必须是 JSON 对象')

    method = payload.get('method')
    if not isinstance(method, str) or not method:
        raise OfflineFramePolicyError('离线帧缺少 method')
    policy = require_registered_offline_method(method)

    params = payload.get('params', {})
    if not isinstance(params, dict):
        raise OfflineFramePolicyError(f'离线帧 params 必须是对象：{method}')
    identity = _stable_identity(params, policy.identity_fields)
    if policy.identity_fields and identity is None:
        fields = ', '.join(policy.identity_fields)
        raise OfflineFramePolicyError(f'离线帧缺少稳定身份：{method}，需要 {fields}')

    return OfflineFrameDecision(
        method=method,
        category=policy.category,
        identity=identity,
        policy=policy,
    )


def decide_offline_storage(
    payload_json: str,
    recovery_mode: str,
) -> OfflineStorageAction:
    """依据恢复模式决定是否写 Redis offline。

    ``sync`` 对未补齐的 durable 缺口必须失败，避免配置切换把真实命令静默丢掉。
    """
    if recovery_mode not in {'redis', 'dual', 'sync'}:
        raise OfflineFramePolicyError(f'离线恢复模式非法：{recovery_mode}')
    decision = classify_offline_frame(payload_json)
    if decision.category is OfflineFrameCategory.TRANSIENT:
        return OfflineStorageAction.SKIP
    if recovery_mode in {'redis', 'dual'}:
        return OfflineStorageAction.STORE
    if decision.category is OfflineFrameCategory.GAP:
        raise OfflineFramePolicyError(f'sync 模式尚有 durable 缺口：{decision.method}')
    return OfflineStorageAction.SKIP


__all__ = [
    'OFFLINE_FRAME_POLICIES',
    'OfflineFrameCategory',
    'OfflineFrameDecision',
    'OfflineFramePolicy',
    'OfflineFramePolicyError',
    'OfflineStorageAction',
    'classify_offline_frame',
    'decide_offline_storage',
    'require_registered_offline_method',
]
