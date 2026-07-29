"""B5 — Umeng U-Push PushDispatcher 契约测试.

覆盖 B5 acceptance:
1. dispatch() 内签名算法 = md5(POST + url + post-body + app_master_secret)
2. 正常路径 → _http_post 被调 1 次 + URL 含正确 sign 查询参数
3. 正常路径 → body 符合 Umeng unicast/listcast 契约 (单 token=unicast, 多=listcast)
4. payload 不携带真实消息明文 (不变式 §4) — 只透传 title/body/trace_id
5. 连续 3 次 httpx.HTTPError → 抛 PushDispatchError
6. 无 push_token → sent=0, 跳过 HTTP 调用 (不算失败)

测试解耦策略:
- monkeypatch `push_dispatcher._http_post` 为假 async 函数, 返回 httpx.Response
  (状态码 + body 可配) — 等价于 mock httpx.post 但更易控制
- monkeypatch `push_dispatcher._load_device_tokens` 为假 async 函数, 避免
  aiosqlite 依赖 (与 B4 test_push_tokens.py 解耦策略一致)
- monkeypatch `asyncio.sleep` 为 no-op, 让重试测试秒级完成
"""
from __future__ import annotations

import asyncio
import hashlib
import json

from types import SimpleNamespace as _SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from backend.app.services import push_dispatcher
from backend.common.observability.prometheus import (
    PUSH_DISPATCHED_TOTAL,
    PUSH_LATENCY_SECONDS,
)

FAKE_HASN_ID = 'h_100001'
FAKE_SECRET = 'test_app_master_secret_42'
FAKE_APP_KEY = 'test_app_key'
FAKE_URL = 'https://msg.umeng.com/api/send'
SimpleNamespace: Any = _SimpleNamespace


def _make_response(status_code: int, body: dict[str, Any]) -> httpx.Response:
    return httpx.Response(status_code=status_code, json=body)


@pytest.fixture
def patched_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入确定性 Umeng 配置."""
    monkeypatch.setattr(
        push_dispatcher.settings, 'UMENG_APP_KEY', FAKE_APP_KEY, raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings,
        'UMENG_APP_MASTER_SECRET',
        FAKE_SECRET,
        raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings, 'UMENG_PUSH_API_URL', FAKE_URL, raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings,
        'UMENG_PUSH_TIMEOUT_SECONDS',
        5.0,
        raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings, 'UMENG_PUSH_MAX_RETRIES', 3, raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings,
        'UMENG_PUSH_BACKOFF_BASE_SECONDS',
        0.0,
        raising=False,
    )
    monkeypatch.setattr(
        push_dispatcher.settings,
        'UMENG_PUSH_PRODUCTION_MODE',
        False,
        raising=False,
    )


def _patch_tokens(
    monkeypatch: pytest.MonkeyPatch, tokens: list[str],
) -> None:
    async def fake_load(db: Any, hasn_id: str) -> list[str]:  # noqa: RUF029
        return list(tokens)

    monkeypatch.setattr(push_dispatcher, '_load_device_tokens', fake_load)


def _patch_http_post(
    monkeypatch: pytest.MonkeyPatch, mock: AsyncMock,
) -> None:
    monkeypatch.setattr(push_dispatcher, '_http_post', mock)


def _patch_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def no_sleep(_: float) -> None:  # noqa: RUF029 — matches awaited signature
        return None

    monkeypatch.setattr(push_dispatcher.asyncio, 'sleep', no_sleep)


def test_umeng_signature_formula() -> None:
    """签名 = md5(method + url + body + secret), 验证字节精确."""
    body = '{"appkey":"x","foo":"bar"}'
    expected = hashlib.md5(
        f'POST{FAKE_URL}{body}{FAKE_SECRET}'.encode(),
        usedforsecurity=False,
    ).hexdigest()
    got = push_dispatcher._umeng_signature(
        method='POST', url=FAKE_URL, body=body, secret=FAKE_SECRET,
    )
    assert got == expected
    assert len(got) == 32


def test_build_umeng_body_unicast_and_listcast() -> None:
    """1 token → unicast; 多 token → listcast; device_tokens 逗号分隔."""
    unicast = push_dispatcher.build_umeng_body(
        device_tokens=['tokA'],
        payload={'title': '唤星', 'body': '你有新消息', 'trace_id': 't1'},
        app_key=FAKE_APP_KEY,
        production_mode=False,
    )
    assert unicast['type'] == 'unicast'
    assert unicast['device_tokens'] == 'tokA'
    assert unicast['appkey'] == FAKE_APP_KEY
    assert unicast['production_mode'] == 'false'
    assert unicast['payload']['display_type'] == 'notification'
    assert unicast['payload']['extra']['trace_id'] == 't1'

    listcast = push_dispatcher.build_umeng_body(
        device_tokens=['tokA', 'tokB', 'tokC'],
        payload={'title': 'x', 'body': 'y', 'trace_id': 't2'},
        app_key=FAKE_APP_KEY,
        production_mode=True,
    )
    assert listcast['type'] == 'listcast'
    assert listcast['device_tokens'] == 'tokA,tokB,tokC'
    assert listcast['production_mode'] == 'true'


def test_build_umeng_body_invariant_no_message_content() -> None:
    """不变式 §4: payload 只透传 title/body/trace_id, 禁止出现原消息明文字段."""
    built = push_dispatcher.build_umeng_body(
        device_tokens=['tokA'],
        payload={
            'title': '唤星',
            'body': '你有新消息',
            'trace_id': 't1',
            'raw_message': '绝对不能出现的明文',
            'conversation_id': 'conv_123',
        },
        app_key=FAKE_APP_KEY,
        production_mode=False,
    )
    serialized = json.dumps(built, ensure_ascii=False)
    assert '绝对不能出现的明文' not in serialized
    assert 'conv_123' not in serialized


def test_dispatch_no_tokens_returns_skipped(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """hasn_id 无 token → sent=0, _http_post 不被调用."""
    _patch_tokens(monkeypatch, [])
    mock_post = AsyncMock()
    _patch_http_post(monkeypatch, mock_post)

    result = asyncio.run(
        push_dispatcher.dispatch(
            SimpleNamespace(),
            hasn_id=FAKE_HASN_ID,
            payload={'title': '唤星', 'body': '新消息', 'trace_id': 'tr1'},
        )
    )

    assert result.sent == 0
    assert result.skipped == 0
    mock_post.assert_not_awaited()


def test_dispatch_success_calls_http_post_once_with_correct_signature(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """正常路径: _http_post 被调 1 次 + URL 含正确 sign 签名参数 + body 可被签名验算."""
    _patch_tokens(monkeypatch, ['deviceTokenAlpha'])

    mock_post = AsyncMock(
        return_value=_make_response(
            200, {'ret': 'SUCCESS', 'data': {'task_id': 'tsk_123'}},
        ),
    )
    _patch_http_post(monkeypatch, mock_post)

    result = asyncio.run(
        push_dispatcher.dispatch(
            SimpleNamespace(),
            hasn_id=FAKE_HASN_ID,
            payload={'title': '唤星', 'body': '新消息', 'trace_id': 'tr9'},
        )
    )

    assert result.sent == 1
    assert result.task_id == 'tsk_123'
    assert mock_post.await_count == 1

    call_args = mock_post.await_args
    assert call_args is not None
    called_url: str = call_args.args[0]
    called_body: bytes = call_args.args[1]
    called_headers: dict[str, str] = call_args.args[2]

    assert called_url.startswith(f'{FAKE_URL}?sign=')
    sign_param = called_url.split('?sign=')[1]
    expected_sign = hashlib.md5(
        f'POST{FAKE_URL}{called_body.decode("utf-8")}{FAKE_SECRET}'.encode(),
        usedforsecurity=False,
    ).hexdigest()
    assert sign_param == expected_sign
    assert called_headers['Content-Type'].startswith('application/json')

    body_json = json.loads(called_body)
    assert body_json['appkey'] == FAKE_APP_KEY
    assert body_json['type'] == 'unicast'
    assert body_json['device_tokens'] == 'deviceTokenAlpha'


def test_dispatch_three_http_errors_raises_push_dispatch_error(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """连续 UMENG_PUSH_MAX_RETRIES (=3) 次 httpx.HTTPError → PushDispatchError."""
    _patch_tokens(monkeypatch, ['tokA'])
    _patch_sleep(monkeypatch)

    mock_post = AsyncMock(
        side_effect=httpx.ConnectError(
            'boom', request=httpx.Request('POST', FAKE_URL),
        ),
    )
    _patch_http_post(monkeypatch, mock_post)

    with pytest.raises(push_dispatcher.PushDispatchError) as exc_info:
        asyncio.run(
            push_dispatcher.dispatch(
                SimpleNamespace(),
                hasn_id=FAKE_HASN_ID,
                payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
            )
        )

    assert mock_post.await_count == 3
    assert 'after 3 attempts' in str(exc_info.value)


def test_dispatch_http_5xx_then_success_recovers(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """首次 502 + 第二次成功 → 不抛, sent=1, _http_post 被调 2 次."""
    _patch_tokens(monkeypatch, ['tokA'])
    _patch_sleep(monkeypatch)

    responses = [
        _make_response(502, {'ret': 'FAIL'}),
        _make_response(200, {'ret': 'SUCCESS', 'data': {'task_id': 'tsk_ok'}}),
    ]
    mock_post = AsyncMock(side_effect=responses)
    _patch_http_post(monkeypatch, mock_post)

    result = asyncio.run(
        push_dispatcher.dispatch(
            SimpleNamespace(),
            hasn_id=FAKE_HASN_ID,
            payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
        )
    )
    assert result.sent == 1
    assert result.task_id == 'tsk_ok'
    assert mock_post.await_count == 2


def test_dispatch_app_level_fail_retries_until_max(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """HTTP 200 但 ret != SUCCESS → 视为失败, 重试到 MAX 后抛."""
    _patch_tokens(monkeypatch, ['tokA'])
    _patch_sleep(monkeypatch)

    mock_post = AsyncMock(
        return_value=_make_response(
            200, {'ret': 'FAIL', 'data': {'error_msg': 'invalid token'}},
        ),
    )
    _patch_http_post(monkeypatch, mock_post)

    with pytest.raises(push_dispatcher.PushDispatchError):
        asyncio.run(
            push_dispatcher.dispatch(
                SimpleNamespace(),
                hasn_id=FAKE_HASN_ID,
                payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
            )
        )
    assert mock_post.await_count == 3


# ---------------------------------------------------------------------------
# B9: Prometheus 指标 (push_dispatched_total / push_latency_seconds)
# ---------------------------------------------------------------------------


def _counter_value(status: str, channel: str = 'umeng_push') -> float:
    """读取 push_dispatched_total{channel, status} 的当前累计值."""
    return PUSH_DISPATCHED_TOTAL.labels(channel=channel, status=status)._value.get()


def _histogram_observations(channel: str = 'umeng_push') -> int:
    """读取 push_latency_seconds{channel} 的 _count sample (观测次数).

    prometheus_client Histogram 的观测次数通过 .collect() 样本暴露为
    `{metric}_count`. 这里遍历所有 children 样本找到匹配 labels 的 _count.
    """
    for metric in PUSH_LATENCY_SECONDS.collect():
        for sample in metric.samples:
            if (
                sample.name.endswith('_count')
                and sample.labels.get('channel') == channel
            ):
                return int(sample.value)
    return 0


def test_dispatch_success_increments_prometheus_counter(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """B9 核心契约: 成功 dispatch → push_dispatched_total{status=success} += 1.

    并验证 push_latency_seconds{channel=umeng_push} 观测次数 += 1 (Histogram).
    """
    _patch_tokens(monkeypatch, ['tokOnly'])
    mock_post = AsyncMock(
        return_value=_make_response(
            200, {'ret': 'SUCCESS', 'data': {'task_id': 'tsk_ok'}},
        ),
    )
    _patch_http_post(monkeypatch, mock_post)

    before_success = _counter_value('success')
    before_hist = _histogram_observations()

    result = asyncio.run(
        push_dispatcher.dispatch(
            SimpleNamespace(),
            hasn_id=FAKE_HASN_ID,
            payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
        )
    )
    assert result.sent == 1

    after_success = _counter_value('success')
    after_hist = _histogram_observations()

    assert after_success == before_success + 1, (
        f'push_dispatched_total{{status=success}} 未自增: '
        f'{before_success} -> {after_success}'
    )
    assert after_hist == before_hist + 1, (
        f'push_latency_seconds 观测次数未自增: {before_hist} -> {after_hist}'
    )


def test_dispatch_skip_when_no_tokens_records_skip_status(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """无 token → push_dispatched_total{status=skip} += 1, http 不被调用."""
    _patch_tokens(monkeypatch, [])
    mock_post = AsyncMock()
    _patch_http_post(monkeypatch, mock_post)

    before_skip = _counter_value('skip')

    result = asyncio.run(
        push_dispatcher.dispatch(
            SimpleNamespace(),
            hasn_id=FAKE_HASN_ID,
            payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
        )
    )
    assert result.sent == 0
    mock_post.assert_not_awaited()

    after_skip = _counter_value('skip')
    assert after_skip == before_skip + 1


def test_dispatch_failure_records_fail_status(
    monkeypatch: pytest.MonkeyPatch, patched_settings: None,
) -> None:
    """重试到 MAX 仍失败 → push_dispatched_total{status=fail} += 1."""
    _patch_tokens(monkeypatch, ['tokA'])
    _patch_sleep(monkeypatch)

    mock_post = AsyncMock(
        side_effect=httpx.ConnectError(
            'boom', request=httpx.Request('POST', FAKE_URL),
        ),
    )
    _patch_http_post(monkeypatch, mock_post)

    before_fail = _counter_value('fail')

    with pytest.raises(push_dispatcher.PushDispatchError):
        asyncio.run(
            push_dispatcher.dispatch(
                SimpleNamespace(),
                hasn_id=FAKE_HASN_ID,
                payload={'title': 'x', 'body': 'y', 'trace_id': 't'},
            )
        )

    after_fail = _counter_value('fail')
    assert after_fail == before_fail + 1


def test_refresh_active_token_gauge_swallows_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DB 查询异常 → 只记 log, 返回 0, 不 raise (可观测性降级 / L8)."""

    class _BoomDb:
        async def execute(self, *_args: Any, **_kw: Any) -> None:
            raise RuntimeError('db offline')

    boom_db: Any = _BoomDb()
    got = asyncio.run(push_dispatcher.refresh_active_token_gauge(boom_db))
    assert got == 0
