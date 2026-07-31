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
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('event_id', 'message_id'),
        durable_source='PostgreSQL integration event + hasn_sync_events(message.recalled)',
        recovery_path='daemon 增量 sync 原子修正消息与会话预览；历史快照返回最终撤回态',
    ),
    OfflineFramePolicy(
        method='hasn.conversation.invalidated',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('event_id', 'conversation_id'),
        durable_source='PostgreSQL integration event + hasn_sync_events(conversation.updated)',
        recovery_path='daemon 按 revision 回源权威投影，SQLite 成功后才推进 sync cursor',
    ),
    OfflineFramePolicy(
        method='hasn.task.exec',
        category=OfflineFrameCategory.DURABLE_SYNC,
        identity_fields=('dispatch_id',),
        durable_source='PostgreSQL hasn_task.task_dispatch_outbox + hasn_sync_events(task.exec)',
        recovery_path='daemon 幂等命令收件箱持久化后推进 cursor，重连与周期任务续跑',
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
        category=OfflineFrameCategory.TRANSIENT,
        identity_fields=(),
        durable_source='PostgreSQL owner 工作台 active_enterprise_id',
        recovery_path='历史兼容通知未被 daemon 消费；工作台读面始终通过权威接口获取，不离线回放',
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


def assert_offline_recovery_mode_supported(recovery_mode: str) -> None:
    """启动期门禁：`sync` 模式不得带着未补齐的 durable 缺口上线。

    缺口原先只在「帧真的要入队」时才抛错，等于把配置错误推迟到某个离线用户的
    请求路径上炸；这里改为进程启动即失败，配置错误在部署窗口内就暴露。

    # Raises
    `OfflineFramePolicyError`：模式非法，或 `sync` 模式仍存在 `gap` 分类帧。
    """
    if recovery_mode not in {'redis', 'dual', 'sync'}:
        raise OfflineFramePolicyError(f'离线恢复模式非法：{recovery_mode}')
    if recovery_mode != 'sync':
        return
    gaps = sorted(
        policy.method for policy in OFFLINE_FRAME_POLICIES.values() if policy.category is OfflineFrameCategory.GAP
    )
    if gaps:
        raise OfflineFramePolicyError('sync 模式尚有 durable 缺口，禁止启动：' + ', '.join(gaps))


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
    'assert_offline_recovery_mode_supported',
    'classify_offline_frame',
    'decide_offline_storage',
    'require_registered_offline_method',
]
