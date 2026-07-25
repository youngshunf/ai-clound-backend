"""NewAPI 内部积分履约客户端（doc94 §3）。

这是云端调用 NewAPI 权威积分能力的**唯一**出站通道，只有三个动作：

- ``put_credit_operation``：以 ``event_id`` 为资源 ID 幂等执行一次发放/回收/订阅生效/订阅到期；
- ``get_credit_operation``：超时后确定性对账（三态语义见下）；
- ``get_credit_account``：读权威余额与当前订阅周期快照（含 ``measured_at``）。

**超时处置的三态语义（outbox worker 的唯一依据）**：

- ``200 + succeeded``：已成功，收敛，不要重投；
- ``200 + failed``：已终局业务失败，进 dead letter，不要重投；
- ``404``：这次操作**确定没有发生**，可以用同一个 ``event_id`` 安全重投。

因此超时后**只能**用同 ``event_id`` 重试或 GET 查询，**禁止换 ID 重发**——换 ID 就等于放弃幂等，
一次网络抖动会变成两次到账。

金额字段一律是十进制积分字符串（最多 5 位小数），不是 quota 微单位，也不用 JSON 浮点数。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from backend.common.log import log
from backend.common.service_http import get_service_client
from backend.core.conf import settings


class NewApiCreditError(Exception):
    """内部履约通道调用失败。

    ``retryable`` 表示 outbox worker 是否应当退避后用**同一个 event_id** 重投：
    网络错误、429、5xx 与 ``credit_store_unavailable`` 可重试；
    其余 4xx 是终局失败，直接进 dead letter。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = 'newapi_credit_transport_error',
        status_code: int | None = None,
        retryable: bool = False,
    ) -> None:
        self.code = code
        self.status_code = status_code
        self.retryable = retryable
        super().__init__(message)


@dataclass(slots=True)
class CreditOperationOutcome:
    """一次履约的终局回执。"""

    event_id: str
    operation_type: str
    status: str
    failure_code: str | None
    applied_credits: str | None
    idempotent_replay: bool
    completed_at: str | None
    account: dict[str, Any] | None
    raw: dict[str, Any]

    @property
    def succeeded(self) -> bool:
        return self.status == 'succeeded'


def _parse_outcome(payload: dict[str, Any]) -> CreditOperationOutcome:
    return CreditOperationOutcome(
        event_id=str(payload.get('event_id') or ''),
        operation_type=str(payload.get('operation_type') or ''),
        status=str(payload.get('status') or ''),
        failure_code=payload.get('failure_code'),
        applied_credits=payload.get('applied_credits'),
        idempotent_replay=bool(payload.get('idempotent_replay')),
        completed_at=payload.get('completed_at'),
        account=payload.get('account'),
        raw=payload,
    )


class NewApiCreditClient:
    """NewAPI ``/api/internal/v1`` 服务到服务客户端。"""

    def __init__(self, *, base_url: str | None = None, service_token: str | None = None, timeout: float | None = None) -> None:
        self.base_url = (base_url or settings.NEWAPI_INTERNAL_BASE_URL).rstrip('/')
        self.service_token = service_token if service_token is not None else settings.NEWAPI_CREDIT_SERVICE_TOKEN
        self.timeout = timeout if timeout is not None else settings.NEWAPI_HTTP_TIMEOUT_SECONDS

    def _headers(self) -> dict[str, str]:
        return {'Authorization': f'Bearer {self.service_token}', 'Content-Type': 'application/json'}

    def _require_credentials(self) -> None:
        if not self.base_url or not self.service_token:
            raise NewApiCreditError(
                'NewAPI 内部履约通道未配置（NEWAPI_INTERNAL_BASE_URL / NEWAPI_CREDIT_SERVICE_TOKEN）',
                code='newapi_credit_not_configured',
                retryable=False,
            )

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, str] | None = None,
    ) -> tuple[int, dict[str, Any]]:
        self._require_credentials()
        client = get_service_client('newapi')
        url = f'{self.base_url}{path}'
        try:
            response = await client.request(
                method, url, headers=self._headers(), json=json, params=params, timeout=self.timeout
            )
        except httpx.HTTPError as exc:
            # 传输层失败：这次操作**可能**发生了也可能没发生，必须靠 GET 对账，不能换 ID 重发。
            raise NewApiCreditError(
                f'NewAPI 内部履约通道不可达: {exc}',
                code='newapi_credit_unreachable',
                retryable=True,
            ) from exc

        try:
            body = response.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        return response.status_code, body

    async def put_credit_operation(self, event_id: str, payload: dict[str, Any]) -> CreditOperationOutcome:
        """幂等执行一次履约。

        200 即终局（succeeded 或 failed）；429/5xx 可退避重投；其余 4xx 终局失败。
        """
        status_code, body = await self._request('PUT', f'/credit-operations/{event_id}', json=payload)
        if status_code == 200:
            return _parse_outcome(body)

        code = str(body.get('code') or f'http_{status_code}')
        message = str(body.get('message') or '')
        retryable = bool(body.get('retryable')) or status_code == 429 or status_code >= 500
        log.warning(f'[NewApiCredit] PUT 履约失败 event_id={event_id} status={status_code} code={code}: {message}')
        raise NewApiCreditError(
            f'NewAPI 履约失败（{status_code} {code}）: {message}',
            code=code,
            status_code=status_code,
            retryable=retryable,
        )

    async def get_credit_operation(self, event_id: str) -> CreditOperationOutcome | None:
        """查询履约终局结果。

        :return: 终局回执；``None`` 表示 404——这次操作确定没有发生，可用同 ``event_id`` 安全重投。
        """
        status_code, body = await self._request('GET', f'/credit-operations/{event_id}')
        if status_code == 200:
            return _parse_outcome(body)
        if status_code == 404:
            return None
        code = str(body.get('code') or f'http_{status_code}')
        raise NewApiCreditError(
            f'NewAPI 履约查询失败（{status_code} {code}）',
            code=code,
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        )

    async def get_credit_account(self, newapi_user_id: int) -> dict[str, Any]:
        """读 NewAPI 权威积分账户快照（含 measured_at）。"""
        status_code, body = await self._request('GET', f'/credit-accounts/{newapi_user_id}')
        if status_code == 200:
            return body
        code = str(body.get('code') or f'http_{status_code}')
        raise NewApiCreditError(
            f'NewAPI 账户查询失败（{status_code} {code}）',
            code=code,
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        )

    async def get_usage_page(
        self,
        newapi_user_id: int,
        *,
        page: int = 1,
        size: int = 20,
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        """读消费流水（doc94 D1：流水的唯一来源）。

        金额由 NewAPI 换算成积分字符串，云端原样透传——云端不再持有 quota↔credit 换算常量，
        也不再维护自己的流水表。两套算法只要不一致，用户看到的流水就和真实扣费对不上。
        """
        params = {'page': str(page), 'size': str(size)}
        if start is not None:
            params['start'] = str(start)
        if end is not None:
            params['end'] = str(end)
        status_code, body = await self._request('GET', f'/credit-usage/{newapi_user_id}', params=params)
        if status_code == 200:
            return body
        code = str(body.get('code') or f'http_{status_code}')
        raise NewApiCreditError(
            f'NewAPI 用量流水查询失败（{status_code} {code}）',
            code=code,
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        )

    async def get_usage_daily(
        self,
        newapi_user_id: int,
        *,
        tz_offset_minutes: int,
        start: int | None = None,
        end: int | None = None,
    ) -> dict[str, Any]:
        """读按本地日聚合的消费。

        日边界由 ``tz_offset_minutes`` 决定并交给 NewAPI 计算：两侧各切各的日，
        同一笔消费会出现在两个不同日期上。
        """
        params = {'tz_offset_minutes': str(tz_offset_minutes)}
        if start is not None:
            params['start'] = str(start)
        if end is not None:
            params['end'] = str(end)
        status_code, body = await self._request('GET', f'/credit-usage/{newapi_user_id}/daily', params=params)
        if status_code == 200:
            return body
        code = str(body.get('code') or f'http_{status_code}')
        raise NewApiCreditError(
            f'NewAPI 日聚合查询失败（{status_code} {code}）',
            code=code,
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        )

    async def get_consumption_summary(self, newapi_user_id: int) -> dict[str, Any]:
        """读历史消费的资金池拆分汇总（doc94 R1 存量 rebase 专用）。

        云端与 NewAPI 的库已解耦，请求日志只在 NewAPI 手上；重建基线所需的
        「真实消费」必须由消费权威给出，不能云端自行推断。

        返回体里的 ``determinate=False`` 表示存在无法归因的历史消费，
        调用方必须把该用户放进人工清单——绝不按比例摊派。
        """
        status_code, body = await self._request('GET', f'/credit-consumption/{newapi_user_id}')
        if status_code == 200:
            return body
        code = str(body.get('code') or f'http_{status_code}')
        raise NewApiCreditError(
            f'NewAPI 消费汇总查询失败（{status_code} {code}）',
            code=code,
            status_code=status_code,
            retryable=status_code == 429 or status_code >= 500,
        )


newapi_credit_client: NewApiCreditClient = NewApiCreditClient()
