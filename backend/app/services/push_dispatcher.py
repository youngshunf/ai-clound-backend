"""M1 移动端推送下发 — Umeng U-Push Server REST API 封装.

依赖规范: docs/架构设计/移动端/04-推送触达与后台运行模型详细设计.md §4.1 / §8.1。

M1 决策: 直接调友盟 HTTP API (https://msg.umeng.com/api/send), 不装第三方
Python SDK (减少依赖, 便于审计). UMENG_APP_MASTER_SECRET 仅后端持有, 绝不下发
到客户端 (CLAUDE.md §不变式 §5); 签名算法 md5(method + url + post-body +
app_master_secret) — 由友盟官方文档 §签名方式 定义。

不变式 §4: 推送 payload 不带消息正文 — 调用方只能传 title/body 摘要 +
trace_id, 真实消息由客户端拉取服务端消息列表 (推送仅唤醒)。

失败重试: 最多 UMENG_PUSH_MAX_RETRIES 次 (默认 3), 指数退避
(base * 2^(attempt-1)); 连续失败抛 PushDispatchError, 由上游 (B6 celery task)
决定是否进死信队列。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import httpx

from sqlalchemy import func, select

from backend.app.hasn_client.model.push_token import PushChannel, PushToken
from backend.common.observability.prometheus import (
    PUSH_DISPATCHED_TOTAL,
    PUSH_LATENCY_SECONDS,
    PUSH_TOKEN_ACTIVE_TOTAL,
)
from backend.core.conf import settings

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


class PushDispatchError(Exception):
    """友盟 U-Push 下发失败 (重试后仍失败)."""


@dataclass(frozen=True)
class DispatchResult:
    """下发结果 (调用方可记入 telemetry)."""

    sent: int
    skipped: int
    task_id: str | None = None


def _umeng_signature(*, method: str, url: str, body: str, secret: str) -> str:
    """Umeng U-Push 签名: md5(method + url + post-body + app_master_secret).

    参考: https://developer.umeng.com/docs/67966/detail/68814#h2-u7B7Eu540Du65B9u5F0F
    """
    raw = f'{method}{url}{body}{secret}'.encode()
    return hashlib.md5(raw, usedforsecurity=False).hexdigest()


def build_umeng_body(
    *,
    device_tokens: list[str],
    payload: dict[str, Any],
    app_key: str,
    production_mode: bool,
) -> dict[str, Any]:
    """构造 Umeng unicast / listcast 请求 body.

    - 单 token → unicast
    - 多 token → listcast (Umeng: device_tokens 逗号分隔, 上限 500)

    payload 最小字段:
      title: str    — 通知栏标题 (默认 '唤星')
      body:  str    — 通知栏正文 (不包含真实消息明文, 不变式 §4)
      trace_id: str — 链路追踪 id (extra 字段透传给客户端)
    """
    title = str(payload.get('title') or '唤星')
    text = str(payload.get('body') or '')
    trace_id = str(payload.get('trace_id') or '')

    cast_type = 'listcast' if len(device_tokens) > 1 else 'unicast'

    return {
        'appkey': app_key,
        'timestamp': str(int(time.time() * 1000)),
        'type': cast_type,
        'device_tokens': ','.join(device_tokens),
        'payload': {
            'display_type': 'notification',
            'body': {
                'ticker': title,
                'title': title,
                'text': text,
                'after_open': 'go_app',
            },
            'extra': {'trace_id': trace_id},
        },
        'policy': {},
        'production_mode': 'true' if production_mode else 'false',
        'description': f'trace:{trace_id}' if trace_id else '',
    }


async def _http_post(
    url: str, body: bytes, headers: dict[str, str], timeout_seconds: float,
) -> httpx.Response:
    """底层 HTTP POST (单独抽出便于测试 monkeypatch).

    生产路径走 httpx.AsyncClient; 测试 monkeypatch 该符号避免真实网络调用.
    """
    async with httpx.AsyncClient(timeout=timeout_seconds) as client:
        return await client.post(url, content=body, headers=headers)


async def _load_device_tokens(db: AsyncSession, hasn_id: str) -> list[str]:
    """查询 hasn_id 名下 umeng_push channel 的全部 device_token."""
    result = await db.execute(
        select(PushToken)
        .where(PushToken.hasn_id == hasn_id)
        .where(PushToken.channel == PushChannel.UMENG_PUSH.value)
    )
    return [row.token for row in result.scalars().all() if row.token]


def _parse_umeng_success(
    response: httpx.Response,
) -> tuple[bool, str | None, str | None]:
    """解析 Umeng 响应.

    Returns (is_success, task_id, error_text).
    - 2xx + ret in ('SUCCESS', None) → success
    - 其他 → 返回 error_text 供日志
    """
    if not 200 <= response.status_code < 300:
        return False, None, response.text
    try:
        data = response.json()
    except ValueError:
        return True, None, None
    if not isinstance(data, dict):
        return True, None, None
    ret = data.get('ret')
    if ret not in ('SUCCESS', None):
        return False, None, response.text
    task_id: str | None = None
    inner = data.get('data')
    if isinstance(inner, dict) and inner.get('task_id') is not None:
        task_id = str(inner['task_id'])
    return True, task_id, None


async def refresh_active_token_gauge(
    db: AsyncSession, channel: str = PushChannel.UMENG_PUSH.value,
) -> int:
    """刷新 push_token_active_total Gauge (B9).

    由 push_tokens 端点 (POST / DELETE) 在 upsert / 删除后调用. 也可用于
    启动探活或定期巡检. 失败仅 log, 不 raise (可观测性降级不影响业务).
    """
    try:
        result = await db.execute(
            select(func.count())
            .select_from(PushToken)
            .where(PushToken.channel == channel)
        )
        count = int(result.scalar() or 0)
    except Exception as exc:
        logger.warning('[push] refresh_active_token_gauge failed: %s', exc)
        return 0
    PUSH_TOKEN_ACTIVE_TOTAL.labels(channel=channel).set(count)
    return count


async def dispatch(
    db: AsyncSession,
    *,
    hasn_id: str,
    payload: dict[str, Any],
) -> DispatchResult:
    """下发一次推送给 hasn_id 的全部 umeng device_tokens.

    - 无 token → 直接返回 sent=0 (不算失败, 不触发重试)
    - 有 token → 构造 listcast/unicast body, 签名, POST 到 U-Push
    - 连续 UMENG_PUSH_MAX_RETRIES 次失败 → PushDispatchError

    可观测性 (B9):
    - push_dispatched_total{channel,status} 每次 dispatch 终态自增一次
      (status ∈ {success, fail, skip})
    - push_latency_seconds{channel} 记录端到端耗时 (秒)
    """
    channel_label = PushChannel.UMENG_PUSH.value
    dispatch_started_at = time.monotonic()
    tokens = await _load_device_tokens(db, hasn_id)
    if not tokens:
        logger.info('[push] hasn_id=%s 无 umeng device_token, 跳过', hasn_id)
        PUSH_DISPATCHED_TOTAL.labels(
            channel=channel_label, status='skip',
        ).inc()
        PUSH_LATENCY_SECONDS.labels(channel=channel_label).observe(
            time.monotonic() - dispatch_started_at,
        )
        return DispatchResult(sent=0, skipped=0)

    body_dict = build_umeng_body(
        device_tokens=tokens,
        payload=payload,
        app_key=settings.UMENG_APP_KEY,
        production_mode=settings.UMENG_PUSH_PRODUCTION_MODE,
    )
    body_str = json.dumps(body_dict, ensure_ascii=False, separators=(',', ':'))
    url = settings.UMENG_PUSH_API_URL
    signature = _umeng_signature(
        method='POST',
        url=url,
        body=body_str,
        secret=settings.UMENG_APP_MASTER_SECRET,
    )
    full_url = f'{url}?sign={signature}'
    body_bytes = body_str.encode('utf-8')
    headers = {'Content-Type': 'application/json; charset=utf-8'}

    max_retries = max(1, int(settings.UMENG_PUSH_MAX_RETRIES))
    backoff_base = float(settings.UMENG_PUSH_BACKOFF_BASE_SECONDS)
    timeout_seconds = float(settings.UMENG_PUSH_TIMEOUT_SECONDS)

    last_exc: Exception | None = None
    last_error_text: str | None = None

    for attempt in range(1, max_retries + 1):
        try:
            response = await _http_post(
                full_url, body_bytes, headers, timeout_seconds,
            )
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.warning(
                '[push] umeng attempt %d/%d network error: %s',
                attempt, max_retries, exc,
            )
        else:
            ok, task_id, err_text = _parse_umeng_success(response)
            if ok:
                logger.info(
                    '[push] umeng dispatched: hasn_id=%s tokens=%d task_id=%s',
                    hasn_id, len(tokens), task_id,
                )
                PUSH_DISPATCHED_TOTAL.labels(
                    channel=channel_label, status='success',
                ).inc()
                PUSH_LATENCY_SECONDS.labels(channel=channel_label).observe(
                    time.monotonic() - dispatch_started_at,
                )
                return DispatchResult(
                    sent=len(tokens), skipped=0, task_id=task_id,
                )
            last_error_text = err_text
            logger.warning(
                '[push] umeng attempt %d/%d fail: status=%s body=%s',
                attempt, max_retries, response.status_code, err_text,
            )

        if attempt < max_retries:
            await asyncio.sleep(backoff_base * (2 ** (attempt - 1)))

    PUSH_DISPATCHED_TOTAL.labels(
        channel=channel_label, status='fail',
    ).inc()
    PUSH_LATENCY_SECONDS.labels(channel=channel_label).observe(
        time.monotonic() - dispatch_started_at,
    )
    raise PushDispatchError(
        f'Umeng U-Push dispatch failed after {max_retries} attempts '
        f'(last_error={last_exc!r}, last_response={last_error_text!r})'
    )
