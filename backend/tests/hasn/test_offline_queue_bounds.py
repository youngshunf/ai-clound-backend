"""方案 B 离线队列边界与 dual 观测窗语义测试。

覆盖三条缺陷修复：

1. 入队必须原子裁剪 + 续期，长期离线实体不得让 Redis 列表无界增长；
2. `dual` 是切 `sync` 前的观测窗，读路径必须继续从 Redis 保护用户；
3. 策略异常位于「业务已提交」的 best-effort 推送路径上，只能记 error 后跳过。
"""

from __future__ import annotations

import json

from typing import Any

import pytest

from backend.app.hasn_im.adapters.routing import node_session_service as module
from backend.app.hasn_im.adapters.routing.redis_presence_store import (
    OFFLINE_MAX_LENGTH,
    OFFLINE_PREFIX,
    OFFLINE_TTL,
)


class OfflineFakeRedis:
    """只实现离线队列所需命令的 Redis 替身，忠实复刻入队 Lua 语义。"""

    def __init__(self) -> None:
        self.lists: dict[str, list[Any]] = {}
        self.expires: dict[str, int] = {}
        self.expire_calls = 0

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:
        assert numkeys == 1
        key = str(args[0])
        if 'RPUSH' in script:
            payload, ttl, max_length = args[1], int(args[2]), int(args[3])
            values = self.lists.setdefault(key, [])
            values.append(payload)
            trimmed = 0
            if max_length > 0 and len(values) > max_length:
                trimmed = len(values) - max_length
                del values[:trimmed]
            self.expires[key] = ttl
            self.expire_calls += 1
            return trimmed
        claimed = list(args[1:])
        values = self.lists.setdefault(key, [])
        if values[: len(claimed)] != claimed:
            return 0
        del values[: len(claimed)]
        return len(claimed)

    async def lrange(self, key: str, start: int, end: int) -> list[Any]:
        values = self.lists.get(key, [])
        return list(values[start:] if end == -1 else values[start : end + 1])

    async def delete(self, key: str) -> int:
        return 1 if self.lists.pop(key, None) is not None else 0


def _frame(message_id: str) -> str:
    return json.dumps({
        'hasn': 'hasn/0.2',
        'method': 'hasn.message.new',
        'params': {'message_id': message_id},
    })


@pytest.fixture
def offline_redis(monkeypatch: pytest.MonkeyPatch) -> OfflineFakeRedis:
    redis = OfflineFakeRedis()
    monkeypatch.setattr(module, 'redis_client', redis)
    return redis


@pytest.mark.asyncio
async def test_offline_queue_is_bounded_and_never_grows_unbounded(
    offline_redis: OfflineFakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """持续离线写入必须被上限截断，且 TTL 由同一原子脚本设置。"""
    monkeypatch.setattr(module.settings, 'HASN_OFFLINE_RECOVERY', 'redis')
    service = module.NodeSessionService()
    key = f'{OFFLINE_PREFIX}:h_flood'

    for index in range(OFFLINE_MAX_LENGTH + 25):
        await service._enqueue_offline('h_flood', _frame(f'msg-{index}'))

    stored = offline_redis.lists[key]
    assert len(stored) == OFFLINE_MAX_LENGTH
    # 保留的是最新的一批，最旧的被丢弃
    assert json.loads(stored[-1])['params']['message_id'] == f'msg-{OFFLINE_MAX_LENGTH + 24}'
    assert json.loads(stored[0])['params']['message_id'] == 'msg-25'
    assert offline_redis.expires[key] == OFFLINE_TTL


@pytest.mark.asyncio
async def test_dual_mode_still_serves_offline_backlog_to_client(
    offline_redis: OfflineFakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """dual 是观测窗：Redis 仍须补推，否则观测期用户已经在丢帧。"""
    monkeypatch.setattr(module.settings, 'HASN_OFFLINE_RECOVERY', 'dual')
    service = module.NodeSessionService()
    await service._enqueue_offline('h_dual', _frame('msg-dual'))

    messages, claims = await service.claim_offline_messages(['h_dual'])
    assert [message['params']['message_id'] for message in messages] == ['msg-dual']
    assert claims

    await service.ack_offline_messages(claims)
    assert offline_redis.lists[f'{OFFLINE_PREFIX}:h_dual'] == []


@pytest.mark.asyncio
async def test_sync_mode_stops_reading_and_writing_offline(
    offline_redis: OfflineFakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """只有 sync 完全交给 PostgreSQL sync/history。"""
    monkeypatch.setattr(module.settings, 'HASN_OFFLINE_RECOVERY', 'sync')
    service = module.NodeSessionService()
    await service._enqueue_offline('h_sync', _frame('msg-sync'))

    assert offline_redis.lists == {}
    assert await service.claim_offline_messages(['h_sync']) == ([], {})
    assert await service.get_offline_messages(['h_sync']) == []


@pytest.mark.asyncio
async def test_policy_error_is_logged_not_raised_into_business_path(
    offline_redis: OfflineFakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """未登记帧不得把已提交的业务写变成 5xx，只能记 error 并跳过入队。"""
    monkeypatch.setattr(module.settings, 'HASN_OFFLINE_RECOVERY', 'redis')
    service = module.NodeSessionService()
    legacy_payload = json.dumps({'cmd': 'MESSAGE_RECALLED', 'msg_id': 7})

    with caplog.at_level('ERROR'):
        await service._enqueue_offline('h_legacy', legacy_payload)

    assert offline_redis.lists == {}
    assert any('离线帧策略拒绝' in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_transient_frame_is_skipped_without_error(
    offline_redis: OfflineFakeRedis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """瞬时帧不入离线队列，也不该被当成异常。"""
    monkeypatch.setattr(module.settings, 'HASN_OFFLINE_RECOVERY', 'redis')
    service = module.NodeSessionService()
    typing_frame = json.dumps({'hasn': 'hasn/0.2', 'method': 'hasn.typing', 'params': {}})

    await service._enqueue_offline('h_typing', typing_frame)

    assert offline_redis.lists == {}
