"""hosting_agent_provider —— 主云端 → hasn-node-hosting agent 的薄 HTTP client（契约 §4）。

**唯一耦合点**：`cloud_node_service` 经本 provider 与 hosting-agent 说话；换宿主 / 多宿主 / 换编排
只动这一层。daemon 不反代 hosting-agent，浏览器也永不直连——这是 cloud-brokered 边界
（父仓 CLAUDE.md §2「独立服务接入主云端采用 cloud-brokered」）。

形态照抄 `backend/app/hasn_quant/provider/engine_client.py`：连接三元组经 `service_endpoint('hosting')`
解析（显式 env/settings → services.toml → dev 本机回落 → prod 留空），Bearer 令牌由
`derive_service_token(master_secret, 'hosting')` 派生，与 agent 侧逐字节一致。

**零 fake**：未配置 / 不可达 / 超时 / 非 JSON / 非 2xx 一律抛 :class:`HostingAgentError`，
由调用方落 `hasn_cloud_nodes.status='failed'` + 真实 `failure_detail`；探活归一成
`{'ok': False, 'error': 'service_unconfigured'|'upstream_error'}`。绝不返回伪造的「容器已就绪」。

agent 契约（详见实施契约 §4）：
- GET    /health                          探活（免鉴权），含 docker 可用性 / 内网禁令 / 备份是否配置
- GET    /v1/capacity                     宿主水位
- POST   /v1/nodes                        建网络+卷+容器并启动（授权码经 tmpfs 注入，禁 -e）
- GET    /v1/nodes/{node_id}              单节点容器视角状态
- POST   /v1/nodes/{node_id}/start|stop|restart
- POST   /v1/nodes/{node_id}/reauthorize  重写 tmpfs secret 并重启，卷不动
- POST   /v1/nodes/{node_id}/update       单节点滚动更新
- DELETE /v1/nodes/{node_id}?purge_volume=true|false
- POST   /v1/nodes/{node_id}/backup       卷快照
- GET    /v1/nodes/{node_id}/logs?tail=N
"""

from __future__ import annotations

from typing import Any

import httpx

from backend.app.hasn_hosting.constants import FAILURE_REASONS, HOSTING_SERVICE_NAME
from backend.common.log import log
from backend.common.service_http import get_service_client
from backend.common.service_registry import service_endpoint


class HostingAgentError(RuntimeError):
    """hosting-agent 传输层失败（未配置 / 不可达 / 超时 / 非 JSON / 非 2xx）。

    调用方据此把 `hasn_cloud_nodes` 落 `failed` 并透传真实 detail——绝不吞掉当成成功。

    `failure_reason` 直接取自 agent 错误体的 `detail.failure_reason`（契约 §2.4 九值之一），
    **不猜**：agent 没给就是 None，由调用方落 `internal_error`，绝不靠字符串匹配臆断原因。
    """

    def __init__(
        self,
        message: str,
        *,
        code: str = 'upstream_error',
        status_code: int | None = None,
        failure_reason: str | None = None,
    ) -> None:
        super().__init__(message)
        #: 机器可判别的错误码（`service_unconfigured` / `upstream_error` / `upstream_timeout` / `upstream_status`）
        self.code = code
        #: agent 返回的 HTTP 状态码（503 docker 不可达 / 404 不存在 / 409 已存在或前置不满足 / 502 拉镜像失败）
        self.status_code = status_code
        #: agent 给出的契约 §2.4 失败原因码；agent 未给则为 None
        self.failure_reason = failure_reason


def _base() -> str:
    return service_endpoint(HOSTING_SERVICE_NAME).base_url


def _timeout() -> float:
    return service_endpoint(HOSTING_SERVICE_NAME).timeout


def _auth_headers() -> dict[str, str]:
    token = service_endpoint(HOSTING_SERVICE_NAME).token
    return {'Authorization': f'Bearer {token}'} if token else {}


def _safe_detail(response: httpx.Response) -> str:
    """尽力从上游响应里取出人可读原因（非 JSON 时截断原文）。"""
    try:
        body = response.json()
    except ValueError:
        return (response.text or '')[:200]
    if isinstance(body, dict):
        return str(body.get('detail') or body.get('message') or body)
    return str(body)


def _failure_reason_of(response: httpx.Response) -> str | None:
    """从 agent 错误体取 `detail.failure_reason`（契约 §4.5 / §2.4）。

    只认 agent 显式给出的九项枚举之一；给了别的值就当没给（宁可落 internal_error，
    也不把一个云端不认识的字符串写进 `hasn_cloud_nodes.failure_reason` 骗 UI 分支）。
    """
    try:
        body = response.json()
    except ValueError:
        return None
    if not isinstance(body, dict):
        return None
    detail = body.get('detail')
    reason = detail.get('failure_reason') if isinstance(detail, dict) else body.get('failure_reason')
    if isinstance(reason, str) and reason in FAILURE_REASONS:
        return reason
    return None


class HostingAgentProvider:
    """调 hosting-agent 内部 HTTP（Bearer 派生令牌 + 超时 + 错误归一）。"""

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        base = _base()
        if not base:
            # prod 未配置时诚实报错，绝不静默连本机掩盖漏配
            raise HostingAgentError(
                '托管宿主服务未配置（HOSTING_AGENT_URL 为空）',
                code='service_unconfigured',
            )
        url = f'{base}{path}'
        try:
            client = get_service_client(HOSTING_SERVICE_NAME)
            resp = await client.request(
                method,
                url,
                json=json_body,
                params=params,
                headers=_auth_headers(),
                timeout=timeout or _timeout(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException as exc:
            raise HostingAgentError(f'托管宿主服务超时: {method} {path}', code='upstream_timeout') from exc
        except httpx.HTTPStatusError as exc:
            detail = _safe_detail(exc.response)
            raise HostingAgentError(
                f'托管宿主服务返回 HTTP {exc.response.status_code}: {detail}',
                code='upstream_status',
                status_code=exc.response.status_code,
                failure_reason=_failure_reason_of(exc.response),
            ) from exc
        except (httpx.HTTPError, ValueError) as exc:
            raise HostingAgentError(
                f'托管宿主服务不可达: {exc.__class__.__name__}',
                code='upstream_error',
            ) from exc
        if not isinstance(data, dict):
            raise HostingAgentError('托管宿主服务返回了非预期格式（期望 JSON object）', code='upstream_error')
        return data

    async def create_node(
        self,
        *,
        node_id: str,
        owner_hasn_id: str,
        authorization_code: str,
        image_ref: str,
        image_digest: str,
        memory_mb: int,
        cpus: float,
        backend_http_base: str,
        backend_ws_url: str,
    ) -> dict[str, Any]:
        """建容器（契约 §4 `POST /v1/nodes`）。

        `authorization_code` 是**明文码**，只在本调用里从云端流向 agent（agent 再经 tmpfs 文件注入
        容器，禁止 `-e`）。云端库里只留 sha256，日志里绝不打印明文。
        """
        return await self._request(
            'POST',
            '/v1/nodes',
            json_body={
                'node_id': node_id,
                'owner_hasn_id': owner_hasn_id,
                'authorization_code': authorization_code,
                'image_ref': image_ref,
                'image_digest': image_digest,
                'resources': {'memory_mb': memory_mb, 'cpus': cpus},
                'backend_http_base': backend_http_base,
                'backend_ws_url': backend_ws_url,
            },
        )

    async def get_node(self, node_id: str) -> dict[str, Any]:
        """取单节点容器视角状态（契约 §4.5⑤）。

        返回含 `daemon_endpoint`（edge 的转发目标 `http://<专属内网IP>:56328`）、
        `backup_configured`、`last_backup_at`、`egress_guard_active`——这四项由云端原样透传，
        不做任何「看起来更好看」的加工（`last_backup_at=null` 就是尚无备份）。
        """
        return await self._request('GET', f'/v1/nodes/{node_id}')

    async def try_get_node(self, node_id: str) -> dict[str, Any] | None:
        """`get_node` 的柔性版本：宿主不可达 / 节点不存在时返回 None 而非抛。

        用于 Owner 面渲染——宿主暂时不通不该让整个详情页 500；对应字段留空即「未知」。
        """
        try:
            return await self.get_node(node_id)
        except HostingAgentError as exc:
            log.warning(f'[hosting] 取容器视角状态失败（详情页按未知渲染）: node_id={node_id}, err={exc}')
            return None

    async def start_node(self, node_id: str) -> dict[str, Any]:
        return await self._request('POST', f'/v1/nodes/{node_id}/start')

    async def stop_node(self, node_id: str) -> dict[str, Any]:
        return await self._request('POST', f'/v1/nodes/{node_id}/stop')

    async def restart_node(self, node_id: str) -> dict[str, Any]:
        return await self._request('POST', f'/v1/nodes/{node_id}/restart')

    async def reauthorize_node(self, node_id: str, *, authorization_code: str) -> dict[str, Any]:
        """重写 tmpfs secret 并重启容器；**数据卷不动**（契约 §3.1 / §4）。"""
        return await self._request(
            'POST',
            f'/v1/nodes/{node_id}/reauthorize',
            json_body={'authorization_code': authorization_code},
        )

    async def update_node(self, node_id: str, *, image_ref: str, image_digest: str) -> dict[str, Any]:
        return await self._request(
            'POST',
            f'/v1/nodes/{node_id}/update',
            json_body={'image_ref': image_ref, 'image_digest': image_digest},
        )

    async def resize_node(
        self, node_id: str, *, memory_mb: int | None = None, cpus: float | None = None
    ) -> dict[str, Any]:
        """改资源档位（H9-b）：镜像与卷都不动，宿主按新规格重建容器。

        两个维度都省略是无意义请求，hosting-agent 会 422 打回——所以调用方必须至少给一个，
        这里不替它兜底成空操作（那会让它以为「改过了」）。
        """
        body: dict[str, Any] = {}
        if memory_mb is not None:
            body['memory_mb'] = memory_mb
        if cpus is not None:
            body['cpus'] = cpus
        return await self._request('POST', f'/v1/nodes/{node_id}/resize', json_body=body)

    async def delete_node(self, node_id: str, *, purge_volume: bool) -> dict[str, Any]:
        return await self._request(
            'DELETE',
            f'/v1/nodes/{node_id}',
            params={'purge_volume': 'true' if purge_volume else 'false'},
        )

    async def capacity(self) -> dict[str, Any]:
        """宿主水位（容器数 / 内存 / CPU / 是否可再建）。"""
        return await self._request('GET', '/v1/capacity')

    async def health(self) -> dict[str, Any]:
        """探活（owner 看板 / 运维诊断用）；不抛，归一成 `ok:false`。"""
        base = _base()
        if not base:
            return {'ok': False, 'error': 'service_unconfigured', 'message': '托管宿主服务未配置'}
        try:
            client = get_service_client(HOSTING_SERVICE_NAME)
            resp = await client.get(f'{base}/health', timeout=5)
            resp.raise_for_status()
            body = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            # 探活失败可重试、不破坏不变量 → warn
            log.warning(f'[hosting] hosting-agent 探活失败: {exc.__class__.__name__}')
            return {'ok': False, 'error': 'upstream_error', 'message': exc.__class__.__name__}
        return {'ok': True, **(body if isinstance(body, dict) else {})}


hosting_agent_provider = HostingAgentProvider()
