"""B6 — push_message Celery task 契约测试.

覆盖 B6 acceptance:
1. 存在 `backend/app/tasks/push_message.py` 里的 `push_message` celery task
2. 1s 窗口合并: Redis SETEX `hasn_push_dedup:{conv_id}` NX EX=1 实现幂等
3. 连续 5 条消息进同一 conversation → push_dispatcher.dispatch 只被调 1 次
4. 不同 conversation 不会互相干扰 (独立 dedup key)
5. 消息不存在 → 返回 'not-found', 不调 dispatch
6. dispatch 返回 sent=0 (无 token) → 返回 'no-token', 不算失败
7. dispatch 抛 PushDispatchError → 返回 'dispatch-error' (不向外抛)
8. 不变式 §4 (payload 不带消息正文): 调 dispatch 的 payload 只含 title/body/trace_id

测试解耦策略 (沿用 B4/B5 模式):
- 不拉 Celery worker: 直接 `asyncio.run(_dispatch_for_message(fake_db, ...))`
- 不连真 Redis: 注入 `_FakeRedis`, `.set()` 实现 SET NX 语义 (1s TTL 在单元测试内忽略)
- 不连 PostgreSQL: mock `hasn_messages_dao.get` 返回 `SimpleNamespace` 伪消息
- 不真调友盟: mock `push_dispatcher.dispatch` 为 AsyncMock, 统计 call_count
"""
from __future__ import annotations

import asyncio

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from backend.app.services.push_dispatcher import DispatchResult, PushDispatchError
from backend.app.tasks import push_message as push_message_module

FAKE_CONV_A = '11111111-1111-1111-1111-111111111111'
FAKE_CONV_B = '22222222-2222-2222-2222-222222222222'
FAKE_HASN_ID = 'h_100001'


class _FakeRedis:
    """最小化实现 redis.asyncio.Redis.set(nx=True, ex=N) 语义."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.set_calls: list[tuple[str, str, bool, int | None]] = []

    async def set(
        self,
        key: str,
        value: str,
        *,
        nx: bool = False,
        ex: int | None = None,
    ) -> bool | None:
        self.set_calls.append((key, value, nx, ex))
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True


def _make_message(message_id: int, conv_id: str, to_id: str = FAKE_HASN_ID) -> SimpleNamespace:
    """伪造 HasnMessages ORM 对象 (只暴露 task 实际访问的字段)."""
    return SimpleNamespace(
        id=message_id,
        conversation_id=conv_id,
        to_id=to_id,
    )


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> _FakeRedis:
    fake = _FakeRedis()
    monkeypatch.setattr(push_message_module, 'redis_client', fake)
    return fake


@pytest.fixture
def mock_dispatch(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=DispatchResult(sent=1, skipped=0, task_id='task-fake'))
    monkeypatch.setattr(push_message_module, 'dispatch', mock)
    return mock


@pytest.fixture
def mock_message_get(monkeypatch: pytest.MonkeyPatch) -> dict[int, SimpleNamespace]:
    """按 message_id 返回预先注册的伪消息, 未注册则返回 None."""
    registry: dict[int, SimpleNamespace] = {}

    async def fake_get(db: Any, pk: int) -> SimpleNamespace | None:  # noqa: RUF029
        return registry.get(pk)

    monkeypatch.setattr(push_message_module.hasn_messages_dao, 'get', fake_get)
    return registry


@pytest.fixture
def fake_db() -> Any:
    """业务代码只把 db 透传给 dispatch, 这里给一个不可用的哨兵即可."""
    return SimpleNamespace(name='fake-db-sentinel')


def test_push_message_celery_task_is_registered() -> None:
    """Acceptance 1: celery task 已注册, name='push_message'."""
    assert hasattr(push_message_module, 'push_message'), 'celery task 导出缺失'
    task = push_message_module.push_message
    assert getattr(task, 'name', None) == 'push_message', (
        f'期望 task.name=push_message, 实得 {getattr(task, "name", None)}'
    )


def test_dedup_key_uses_conversation_id() -> None:
    """Acceptance 2: dedup key 前缀 = 'hasn_push_dedup:{conv_id}'."""
    key = push_message_module._build_dedup_key(FAKE_CONV_A)
    assert key == f'hasn_push_dedup:{FAKE_CONV_A}'
    assert push_message_module.DEDUP_KEY_PREFIX == 'hasn_push_dedup:'
    assert push_message_module.DEDUP_TTL_SECONDS == 1


def test_5_messages_same_conversation_dispatch_once(
    fake_redis: _FakeRedis,
    mock_dispatch: AsyncMock,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 3 (核心): 5 条同 conv 消息 → dispatch 仅被调 1 次, 余 4 条 dedup-skip."""
    for msg_id in range(1001, 1006):
        mock_message_get[msg_id] = _make_message(msg_id, FAKE_CONV_A)

    results: list[str] = []
    for msg_id in range(1001, 1006):
        result = asyncio.run(
            push_message_module._dispatch_for_message(fake_db, msg_id),
        )
        results.append(result)

    sent_results = [r for r in results if r.startswith('sent:')]
    skip_results = [r for r in results if r == 'dedup-skip']

    assert mock_dispatch.call_count == 1, (
        f'期望 dispatch 调 1 次, 实得 {mock_dispatch.call_count}; results={results}'
    )
    assert len(sent_results) == 1, f'期望 1 条成功, 实得 {sent_results}'
    assert len(skip_results) == 4, f'期望 4 条 dedup-skip, 实得 {skip_results}'
    assert sum(1 for _k, _v, nx, _ex in fake_redis.set_calls if nx) == 5, (
        'redis.set 应被调用 5 次且均 nx=True'
    )


def test_different_conversations_dispatch_independently(
    fake_redis: _FakeRedis,
    mock_dispatch: AsyncMock,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 4: 不同 conv 不共享 dedup → 各自下发一次."""
    mock_message_get[2001] = _make_message(2001, FAKE_CONV_A)
    mock_message_get[2002] = _make_message(2002, FAKE_CONV_B)

    asyncio.run(push_message_module._dispatch_for_message(fake_db, 2001))
    asyncio.run(push_message_module._dispatch_for_message(fake_db, 2002))

    assert mock_dispatch.call_count == 2


def test_message_not_found_skips_dispatch(
    fake_redis: _FakeRedis,
    mock_dispatch: AsyncMock,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 5: message_id 不存在 → 'not-found', 不调 dispatch, 不写 redis."""
    result = asyncio.run(
        push_message_module._dispatch_for_message(fake_db, 9999),
    )
    assert result == 'not-found'
    assert mock_dispatch.call_count == 0
    assert fake_redis.set_calls == []


def test_dispatch_returns_no_token_is_not_failure(
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 6: dispatch sent=0 → 'no-token' (不算失败, 不抛)."""
    mock_no_token = AsyncMock(return_value=DispatchResult(sent=0, skipped=0))
    monkeypatch.setattr(push_message_module, 'dispatch', mock_no_token)
    mock_message_get[3001] = _make_message(3001, FAKE_CONV_A)

    result = asyncio.run(
        push_message_module._dispatch_for_message(fake_db, 3001),
    )
    assert result == 'no-token'
    assert mock_no_token.call_count == 1


def test_dispatch_error_swallowed(
    fake_redis: _FakeRedis,
    monkeypatch: pytest.MonkeyPatch,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 7: PushDispatchError 不向 celery worker 抛 (避免自动 retry 重复下发)."""
    mock_fail = AsyncMock(side_effect=PushDispatchError('umeng 5xx after retries'))
    monkeypatch.setattr(push_message_module, 'dispatch', mock_fail)
    mock_message_get[4001] = _make_message(4001, FAKE_CONV_A)

    result = asyncio.run(
        push_message_module._dispatch_for_message(fake_db, 4001),
    )
    assert result == 'dispatch-error'
    assert mock_fail.call_count == 1


def test_payload_does_not_carry_message_content(
    fake_redis: _FakeRedis,
    mock_dispatch: AsyncMock,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """不变式 §4: 推送 payload 不带消息正文 — 只 title/body/trace_id."""
    mock_message_get[5001] = _make_message(5001, FAKE_CONV_A)

    asyncio.run(push_message_module._dispatch_for_message(fake_db, 5001))

    assert mock_dispatch.call_count == 1
    _, kwargs = mock_dispatch.call_args
    payload = kwargs.get('payload')
    assert payload is not None, 'dispatch 必须以 kwargs payload 传入'
    assert set(payload.keys()) == {'title', 'body', 'trace_id'}, (
        f'payload 含多余字段: {set(payload.keys()) - {"title", "body", "trace_id"}}'
    )
    assert 'content' not in payload
    assert 'conversation_id' not in payload
    # trace_id 稳定 (同 conv 同一 trace_id)
    assert payload['trace_id'] == f'conv:{FAKE_CONV_A}'


def test_dedup_redis_set_uses_nx_and_ex(
    fake_redis: _FakeRedis,
    mock_dispatch: AsyncMock,
    mock_message_get: dict[int, SimpleNamespace],
    fake_db: Any,
) -> None:
    """Acceptance 2 细化: set 调用必须带 nx=True + ex=1."""
    mock_message_get[6001] = _make_message(6001, FAKE_CONV_A)
    asyncio.run(push_message_module._dispatch_for_message(fake_db, 6001))

    assert len(fake_redis.set_calls) == 1
    key, value, nx, ex = fake_redis.set_calls[0]
    assert key == f'hasn_push_dedup:{FAKE_CONV_A}'
    assert value == '1'
    assert nx is True
    assert ex == 1
