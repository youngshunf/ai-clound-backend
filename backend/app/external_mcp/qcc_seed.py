"""企查查（qcc）平台 MCP 幂等 seed（P7-E / 获客 read-through 前置）。

把 qcc 的 6 个远程 streamable-HTTP MCP server 作为 **system-origin / remote_service** 注册进
通用第三方 MCP 网关（事实源 10 §4/§6/§7，runbook 实施/100）。注册后：

- 获客 `hasn_growth` 三高层工具经 ``ExternalMcpGateway.call_system_tool``（``hasn.ext.qcc_*.*``）
  以**平台身份**调用（架构A，绕 per-agent binding）；
- 分身亦可经 owner binding + 渐进暴露直接用 ``hasn.ext.qcc_*.*``（架构B，可选）。

token 治理（铁律）：平台 Bearer **绝不**写进代码 / `.env` / `services.toml` / 日志 / 受版本控制文件——
仅经环境变量 ``QCC_BEARER_TOKEN`` 一次性传入、加密落 secret store（``secret://system/qcc_*/credential``）、
出参永不回显。不传 token 时只注册 server 行（introspect 会因无凭据 unhealthy，
补 token 后重跑本 seed 即幂等修复）。

用法（运维一次性，出站需可达 agent.qcc.com；httpx 已 trust_env=False 绕系统代理）::

    QCC_BEARER_TOKEN='***' QCC_PER_OWNER_DAILY_QUOTA=500 QCC_RATE_LIMIT_PER_MIN=30 \\
        uv run python -m backend.app.external_mcp.qcc_seed
"""

from __future__ import annotations

import asyncio
import logging
import os

from dataclasses import dataclass

from backend.app.external_mcp.service.gateway_service import external_mcp_gateway

logger = logging.getLogger(__name__)

# 平台 key 防刷爆保守初值（runbook §4：硬需求不可留 0；上线后按 qcc 套餐 / 活跃 owner 数看账本调）。
DEFAULT_PER_OWNER_DAILY_QUOTA = 500
DEFAULT_RATE_LIMIT_PER_MIN = 30

QCC_MCP_BASE = 'https://agent.qcc.com/mcp'


@dataclass(frozen=True)
class QccServerSpec:
    """一个 qcc 远程 MCP server 的注册规格。"""

    namespace: str
    path: str
    display_name: str

    @property
    def endpoint(self) -> str:
        return f'{QCC_MCP_BASE}/{self.path}/stream'


# 6 个独立 streamable-HTTP MCP server（runbook 实施/100 §1）。
QCC_SERVERS: tuple[QccServerSpec, ...] = (
    QccServerSpec('qcc_company', 'company', '企查查·工商'),
    QccServerSpec('qcc_risk', 'risk', '企查查·风险'),
    QccServerSpec('qcc_ipr', 'ipr', '企查查·知识产权'),
    QccServerSpec('qcc_operation', 'operation', '企查查·经营'),
    QccServerSpec('qcc_executive', 'executive', '企查查·高管'),
    QccServerSpec('qcc_history', 'history', '企查查·变更历史'),
)


async def seed_qcc_servers(
    *,
    bearer_token: str | None = None,
    per_owner_daily_quota: int = DEFAULT_PER_OWNER_DAILY_QUOTA,
    rate_limit_per_min: int = DEFAULT_RATE_LIMIT_PER_MIN,
    introspect: bool = True,
) -> list[dict]:
    """幂等注册 qcc 6 server（已存在则复用 mcp_id，不重复建行）。

    每个 server：``origin=system`` / ``hosting=remote_service`` / ``transport=http``。
    - ``bearer_token`` 提供时写入 / 轮换平台 key（加密落 secret store，is_admin 通道）；
    - ``introspect`` 且已配凭据时拉 ``tools/list`` 缓存（``call_system_tool`` 据此解析 raw_name）。

    返回每个 server 的处理结果（含 ``mcp_id`` / ``action`` / ``credential_written`` /
    ``tools_count`` / ``health``）。
    """
    existing = {s['name']: s for s in await external_mcp_gateway.list_servers_admin(origin='system')}
    results: list[dict] = []
    for spec in QCC_SERVERS:
        if spec.namespace in existing:
            mcp_id = existing[spec.namespace]['mcp_id']
            action = 'existed'
            # 幂等对齐配额（runbook §4 不可留 0）。
            await external_mcp_gateway.set_server_quota(
                mcp_id=mcp_id,
                per_owner_daily_quota=per_owner_daily_quota,
                rate_limit_per_min=rate_limit_per_min,
            )
        else:
            server = await external_mcp_gateway.register_server(
                name=spec.namespace,
                hosting='remote_service',
                transport='http',
                origin='system',
                endpoint=spec.endpoint,
                display_name=spec.display_name,
                risk_level='medium',
                per_owner_daily_quota=per_owner_daily_quota,
                rate_limit_per_min=rate_limit_per_min,
            )
            mcp_id = server['mcp_id']
            action = 'registered'

        credential_written = False
        if bearer_token:
            await external_mcp_gateway.set_credential(
                mcp_id=mcp_id,
                plaintext=bearer_token,
                is_admin=True,
                auth_header='Authorization',
                auth_scheme='Bearer',
            )
            credential_written = True

        tools_count: int | None = None
        health: str | None = None
        if introspect and credential_written:
            intro = await external_mcp_gateway.introspect_server(mcp_id)
            tools_count = len(intro.get('tools') or [])
            health = intro.get('health')

        results.append(
            {
                'namespace': spec.namespace,
                'mcp_id': mcp_id,
                'endpoint': spec.endpoint,
                'action': action,
                'credential_written': credential_written,
                'tools_count': tools_count,
                'health': health,
            }
        )
        logger.info(
            'qcc seed: %s %s (cred=%s tools=%s health=%s)',
            spec.namespace,
            action,
            credential_written,
            tools_count,
            health,
        )
    return results


def _main() -> None:
    logging.basicConfig(level=logging.INFO)
    token = os.environ.get('QCC_BEARER_TOKEN') or None
    quota = int(os.environ.get('QCC_PER_OWNER_DAILY_QUOTA', DEFAULT_PER_OWNER_DAILY_QUOTA))
    rate = int(os.environ.get('QCC_RATE_LIMIT_PER_MIN', DEFAULT_RATE_LIMIT_PER_MIN))
    if not token:
        logger.warning(
            '未设置 QCC_BEARER_TOKEN：只注册 server 行，不写凭据 / 不自省'
            '（补 token 后重跑本 seed 幂等修复）'
        )
    results = asyncio.run(
        seed_qcc_servers(bearer_token=token, per_owner_daily_quota=quota, rate_limit_per_min=rate)
    )
    for r in results:
        print(
            f"  {r['namespace']:16s} {r['action']:10s} "
            f"cred={'Y' if r['credential_written'] else 'N'} "
            f"tools={r['tools_count']} health={r['health']} mcp_id={r['mcp_id']}"
        )


if __name__ == '__main__':
    _main()
