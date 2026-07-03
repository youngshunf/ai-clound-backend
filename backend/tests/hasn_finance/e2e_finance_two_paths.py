"""FIN-S6 — finance 全栈真实跨服务 E2E（两路·零 mock）。

不是 pytest 收集用例（无 `test_` 前缀，需活体数据服务，按需手跑）：

    cd huanxing-cloud-backend && .venv/bin/python backend/tests/hasn_finance/e2e_finance_two_paths.py

做什么（真实，零 mock / 零 fake）：
1. 用 finance-data-service 自带的 `.venv-s0`（含真实 akshare 1.18.64）起一个**真实**数据服务进程
   （uvicorn，FIN_SVC_TOKEN 令牌闸开，内存缓存兜底，临时历史库）。
2. 用**云端真实代码**驱动**两条路**打这个真实服务（唯一耦合点 `finance_provider`）：
   - Agent MCP 路：`finance_tool_handlers.handle_*`（gateway_internal 进程内直调的真 handler）。
   - Owner 看板路：`api/v1/app/finance.py` 的真 owner handler 函数（webui 经 daemon 薄代理打的就是它）。
   两路都收敛到同一 `finance_provider` → 真实 akshare 出真数据。
3. 韧性/三态/安全：服务挂掉→诚实 upstream_error（非 fake）；错 token→401 归一；未配置→service_unconfigured；
   push2 受限接口→真实 upstream_error 透传。

证据写 `huanxing-project/test-results/finance-e2e/S6_two_paths.{json,md}`。

注：finance:read 三态「deny」由 `ai_native_runtime_gateway` 的 capability_modes 在调 handler **之前**裁决
（与 growth/creator 同框架，平台测试已覆盖），handler 本身按设计无 per-owner 闸门——故本数据路 E2E
不重复造 gateway，只在文档里点明 deny 由上游统一框架强制。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import sys
import time

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

# ── 路径解析（cloud backend repo → 兄弟 huanxing-project 下的 apps / test-results）──
THIS = Path(__file__).resolve()
CLOUD_REPO = THIS.parents[3]  # .../huanxing-cloud-backend
PROJECT = CLOUD_REPO.parent  # .../huanxing-project
DATA_SVC = PROJECT / 'huanxing-apps' / 'finance-data-service'
DATA_VENV_PY = DATA_SVC / '.venv-s0' / 'bin' / 'python'
EVIDENCE_DIR = PROJECT / 'test-results' / 'finance-e2e'

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str) -> None:
    RESULTS.append({'scenario': name, 'pass': ok, 'detail': detail})
    print(f'[{"PASS" if ok else "FAIL"}] {name}: {detail}', flush=True)


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return int(s.getsockname()[1])


def wait_healthz(base: str, timeout_s: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f'{base}/v1/healthz', timeout=3)
            if r.status_code == 200 and r.json().get('ok') is True:
                return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def boot_data_service(port: int, token: str) -> subprocess.Popen[bytes]:
    if not DATA_VENV_PY.exists():
        raise SystemExit(f'finance-data-service venv 缺失: {DATA_VENV_PY}（先 setup .venv-s0）')
    env = os.environ.copy()
    env['FIN_SVC_TOKEN'] = token
    env['FIN_REDIS_URL'] = ''  # 强制走进程内 LRU（无 Redis 依赖）
    env['FIN_HISTORY_DB'] = str(DATA_SVC / 'data' / 'e2e_s6_history.db')
    env['FIN_ALLOWED_HOSTS'] = ''  # 不限 Host（本机回环）
    return subprocess.Popen(
        [str(DATA_VENV_PY), '-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', str(port)],
        cwd=str(DATA_SVC),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def set_setting(settings: Any, key: str, value: Any) -> None:
    """运行时改 settings（pydantic 实例）——兼容 validate_assignment 开/关。"""
    try:
        setattr(settings, key, value)
    except Exception:
        object.__setattr__(settings, key, value)


async def run_paths(base: str, token: str) -> None:
    # 关键：先把 env 配好再 import settings（首次实例化即读 env），运行时再按场景改属性。
    os.environ['FINANCE_SERVICE_URL'] = base
    os.environ['FINANCE_SERVICE_TOKEN'] = token
    os.environ['FINANCE_SERVICE_TIMEOUT'] = '30'

    from backend.app.hasn_finance.api.v1.app import finance as owner_api
    from backend.app.hasn_finance.provider import finance_provider
    from backend.app.hasn_finance.service import finance_tool_handlers as H
    from backend.core.conf import settings

    set_setting(settings, 'FINANCE_SERVICE_URL', base)
    set_setting(settings, 'FINANCE_SERVICE_TOKEN', token)

    # 探活（owner 看板诊断用的真 healthz 路）。
    health = await finance_provider.healthz()
    record('healthz', health.get('ok') is True, f'ok={health.get("ok")} interfaces={health.get("interfaces")}')

    # ── 路①Agent MCP：真 handler → provider → 真 akshare（宏观 CPI，sandbox 内可达）──
    a = await H.handle_macro_indicator(None, None, {'indicator': 'cpi', 'limit': 3})
    a_ok = (
        a.get('ok') is True
        and isinstance(a.get('columns'), list)
        and isinstance(a.get('rows'), list)
        and len(a.get('rows', [])) > 0
    )
    record(
        'agent_path.macro_indicator(cpi)',
        a_ok,
        f'ok={a.get("ok")} count={a.get("count")} cols={len(a.get("columns", []))} '
        f'sample_col={(a.get("columns") or [None])[0]!r}',
    )

    # ── 路①Agent MCP：财务摘要（datacenter，sandbox 内可达）──
    fin = await H.handle_stock_financial(None, None, {'symbol': '600519', 'limit': 2})
    record(
        'agent_path.stock_financial(600519)',
        fin.get('ok') is True and len(fin.get('rows', [])) > 0,
        f'ok={fin.get("ok")} count={fin.get("count")}',
    )

    # ── 路②Owner 看板：真 owner handler 函数（webui 经 daemon 打的就是它）──
    macro_resp = await owner_api.macro_indicator(indicator='cpi', limit=2)
    macro_data = macro_resp.data
    record(
        'owner_path.macro_indicator(cpi)',
        bool(macro_data) and macro_data.get('ok') is True and len(macro_data.get('rows', [])) > 0,
        f'envelope.code={macro_resp.code} data.ok={macro_data.get("ok") if macro_data else None}',
    )

    # 龙虎榜日期区间必填（akshare stock_lhb_detail_em 需窗口）；webui 兜底近 30 天，这里同口径。
    today = datetime.now()  # noqa: DTZ005 — 本地墙钟，与 webui defaultBillboardRange 同口径
    end_date = today.strftime('%Y%m%d')
    start_date = (today - timedelta(days=30)).strftime('%Y%m%d')
    bill_resp = await owner_api.stock_billboard(start_date=start_date, end_date=end_date, limit=5)
    bill_data = bill_resp.data
    # 有效窗口可能恰好无龙虎榜数据 → 合法的 ok:true count:0；故断言 ok:true（成功取数）而非 rows>0。
    record(
        'owner_path.stock_billboard(近30天)',
        bool(bill_data) and bill_data.get('ok') is True,
        f'window={start_date}~{end_date} data.ok={bill_data.get("ok") if bill_data else None} '
        f'count={bill_data.get("count") if bill_data else None}',
    )

    # ── push2 受限接口（实时）：真实 upstream_error 透传（沙箱出网受限；零 fake）──
    rt = await H.handle_stock_realtime(None, None, {'symbols': '600519'})
    rt_honest = rt.get('ok') is False and rt.get('error') in {'upstream_error', 'upstream_timeout'}
    record(
        'agent_path.stock_realtime(push2 受限→诚实错误)',
        rt_honest,
        f'ok={rt.get("ok")} error={rt.get("error")} msg={str(rt.get("message"))[:60]!r}',
    )

    # ── 安全：错 token → 服务 401 → provider 归一 upstream_error（不抛、不 fake）──
    set_setting(settings, 'FINANCE_SERVICE_TOKEN', 'WRONG-' + secrets.token_hex(4))
    bad = await finance_provider.query('macro.indicator', {'indicator': 'cpi'})
    record(
        'security.wrong_token→401 归一',
        bad.get('ok') is False and bad.get('error') == 'upstream_error' and '401' in str(bad.get('message')),
        f'ok={bad.get("ok")} error={bad.get("error")} msg={bad.get("message")!r}',
    )
    set_setting(settings, 'FINANCE_SERVICE_TOKEN', token)  # 复原

    # ── 配置缺失：URL 空 → service_unconfigured（诚实，不打任何网络）──
    set_setting(settings, 'FINANCE_SERVICE_URL', '')
    unconf = await finance_provider.query('macro.indicator', {'indicator': 'cpi'})
    record(
        'config.unconfigured→service_unconfigured',
        unconf.get('ok') is False and unconf.get('error') == 'service_unconfigured',
        f'ok={unconf.get("ok")} error={unconf.get("error")}',
    )
    set_setting(settings, 'FINANCE_SERVICE_URL', base)  # 复原供下一步


async def run_resilience_after_kill() -> None:
    """服务已被杀死后调用：provider 应诚实归一 upstream_error / upstream_timeout（非 fake）。"""
    from backend.app.hasn_finance.provider import finance_provider

    down = await finance_provider.query('macro.indicator', {'indicator': 'cpi'})
    record(
        'resilience.service_down→诚实 upstream_error',
        down.get('ok') is False and down.get('error') in {'upstream_error', 'upstream_timeout'},
        f'ok={down.get("ok")} error={down.get("error")} msg={str(down.get("message"))[:60]!r}',
    )


def main() -> int:
    port = free_port()
    token = secrets.token_hex(16)  # 临时本地令牌（非生产密钥，进程级随机生成）
    base = f'http://127.0.0.1:{port}'
    print(f'== 起真实 finance-data-service @ {base}（真 akshare）==', flush=True)
    proc = boot_data_service(port, token)
    try:
        if not wait_healthz(base):
            record('boot', False, 'healthz 30s 内未就绪')
            raise SystemExit(_finish(2))
        record('boot', True, f'data-service 就绪 @ {base}')
        asyncio.run(run_paths(base, token))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    # 服务已停 → 韧性场景。
    time.sleep(0.5)
    asyncio.run(run_resilience_after_kill())
    return _finish(0)


def _finish(boot_rc: int) -> int:
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    passed = sum(1 for r in RESULTS if r['pass'])
    total = len(RESULTS)
    all_ok = boot_rc == 0 and passed == total and total > 0
    (EVIDENCE_DIR / 'S6_two_paths.json').write_text(
        json.dumps({'all_pass': all_ok, 'passed': passed, 'total': total, 'results': RESULTS}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    lines = [
        '# FIN-S6 finance 全栈真实跨服务 E2E（两路·零 mock）证据',
        '',
        f'结果：**{passed}/{total} PASS**（all_pass={all_ok}）。真实 finance-data-service（真 akshare 1.18.64）'
        '+ 云端真实 `finance_provider`/handler 驱动两路。',
        '',
        '| 场景 | 结果 | 详情 |',
        '|---|---|---|',
    ]
    lines.extend(f'| {r["scenario"]} | {"✅" if r["pass"] else "❌"} | {r["detail"]} |' for r in RESULTS)
    lines += [
        '',
        '## 说明',
        '- **两路同源**：Agent MCP 路（`finance_tool_handlers.handle_*`）与 Owner 看板路（`api/v1/app/finance.py`）'
        '都收敛到同一 `finance_provider`（唯一耦合点），本 E2E 两路分别真实出数。',
        '- **零 fake**：push2 受限接口（实时/历史 K 线）在本沙箱出网受限 → 触发**真实** upstream_error 透传'
        '（设计 §4 注脚：生产国内区云须复验 push2*.eastmoney.com 可达性）；datacenter/sina 系（宏观/财务/龙虎榜）真实出数。',
        '- **三态 deny**：finance:read 的 deny 由 `ai_native_runtime_gateway` capability_modes 在调 handler **之前**裁决'
        '（与 growth/creator 同框架，平台注册测试已覆盖），handler 按设计无 per-owner 闸门，故此处不重复造 gateway。',
        '- **daemon/webui 层**：daemon 纯透传（`finance_owner_proxy` 6 契约测试，httpmock 立云端）+ webui 只调 daemon'
        '（finance 23 单测，mock daemon）已分层覆盖；本 E2E 补齐**真实跨服务数据路**这一无法 mock 的环节。',
    ]
    (EVIDENCE_DIR / 'S6_two_paths.md').write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\n== {passed}/{total} PASS == 证据写入 {EVIDENCE_DIR}/S6_two_paths.{{json,md}}', flush=True)
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.exit(main())
