r"""内部服务健康状态 CLI —— 终端一行命令看全部内部独立服务死活（复用 service_registry + service_health）。

与管理端「系统监控 → 内部服务」页同一数据源（service_health.check_all_services_health），
免去逐个 curl，也不需要 Admin JWT（直接进程内探活）。

用法：
    PYTHONPATH=backend uv run python -m backend.scripts.services_status

退出码：有任一服务不可达(down) → 1；否则 0（unconfigured=未配置不发网络、不算 down）。
配置来源同主云端 settings：dev 未配的服务回落本机约定端口探活（见 service_registry）。
"""

from __future__ import annotations

import asyncio
import sys

from backend.common.service_health import check_all_services_health

_ICON = {'up': '✓', 'down': '✗', 'unconfigured': '·'}


async def _main() -> int:
    reports = await check_all_services_health()
    print('内部服务健康（service_registry 目录；dev 未配回落本机约定端口）：')
    print(f'{"":2}{"服务":<14}{"状态":<8}{"延迟":<8}{"地址":<34}说明')
    down = 0
    for r in reports:
        if r.status == 'down':
            down += 1
        icon = _ICON.get(r.status, '?')
        latency = '—' if r.latency_ms is None else f'{r.latency_ms}ms'
        version = f' v{r.version}' if r.version else ''
        print(
            f'{icon} {r.title:<14}{r.status:<8}{latency:<8}'
            f'{(r.base_url or "(未配置)"):<34}{r.detail}{version}'
        )
    if down:
        print(f'\n⚠️  {down} 个服务不可达。')
    return 1 if down else 0


def main() -> None:
    sys.exit(asyncio.run(_main()))


if __name__ == '__main__':
    main()
