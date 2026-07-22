#!/usr/bin/env python3
"""doc35 E2 全栈活体 E2E：产物四维度分类体系 + 产出闸（零 mock）。

**不是 pytest**（文件名刻意不带 `test_` 前缀，避免被收集）——它要真 Redis / 真云端 /
真 PG 都在跑才有意义，跑法见文末。

真链路：
    真 Redis 种验证码 → 真云端登录取 Agent JWT → 真云端 MCP streamable 调 hasn.deck.*
    → 云端 register-on-write 自动登记 → 真 PG 断言四维度 → 真 output_gate 判定产出闸

断言 A·四维度（doc35 §2）——登记侧填对了，闸才比得上：
    - kind(=artifact_kind) == 'resource'      怎么打开（6 闭集）
    - resource_kind == 'deck.presentation'    是什么（{app}.{kind}，18 条权威值）
    - source_app_id == 'deck'                 谁产出的
    - source_kind == 'app'                    怎么来的（6 枚举）
    - resource_uri == 'hasn://deck/{云端权威 id}'（本地 ID 永不上 URI）
    - session_id 绑上工作会话（漏了则产物挂不进会话资源栏）

断言 B·产出闸（doc35 §0.2）——喂 A 步真登记的产物形状进共享纯函数 `output_gate.satisfies`。

前置：
    - 云端后端跑在 :8020（**改过 doc35 相关代码后必须重启**，否则跑的是陈旧进程）
    - 本地 PG :15432 库 huanxing、Redis 按 backend/.env 的 REDIS_* 配置（非默认实例）
    - DOC35_PHONE 指定的账号名下**至少有一个分身**

跑法：
    python3 tests/doc35_artifact_taxonomy_live_e2e.py
    DOC35_PHONE=18510813826 python3 tests/doc35_artifact_taxonomy_live_e2e.py
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import uuid
from pathlib import Path

import httpx

CLOUD = os.environ.get('DOC35_CLOUD', 'http://127.0.0.1:8020')
PHONE = os.environ.get('DOC35_PHONE', '18611348367')
CODE = '123456'

BACKEND_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ENV = BACKEND_ROOT / 'backend' / '.env'
VENV_PYTHON = BACKEND_ROOT / '.venv' / 'bin' / 'python'

PG_ARGS = ['-h', '127.0.0.1', '-p', '15432', '-U', 'postgres', '-d', 'huanxing']

# 本次跑的工作会话 id（模拟系统注入的 _hasn_session_id，验证产物绑会话）
SESSION_ID = f'doc35-e2e-{uuid.uuid4().hex[:12]}'


def _env(key: str, default: str = '') -> str:
    """从云端 .env 读配置。

    Redis **不是默认实例**（端口/密码/db 都在 .env 里），种到默认 6379 云端读不到，
    表现为登录返回「验证码已过期」——踩过，故这里必须读真配置。
    """
    try:
        for line in BACKEND_ENV.read_text(encoding='utf-8').splitlines():
            line = line.strip()
            if line.startswith(f'{key}='):
                return line.split('=', 1)[1].strip().strip("'").strip('"')
    except OSError:
        pass
    return default


def seed_sms_code(phone: str) -> None:
    """把验证码种进云端真 Redis（key 前缀见 hasn_onboarding_service.SMS_CODE_PREFIX）。"""
    args = ['redis-cli', '-h', _env('REDIS_HOST', '127.0.0.1'), '-p', _env('REDIS_PORT', '6379')]
    password = _env('REDIS_PASSWORD')
    if password:
        args += ['-a', password, '--no-auth-warning']
    args += ['-n', _env('REDIS_DATABASE', '0'), 'setex', f'sms_code:{phone}', '1800', CODE]
    out = subprocess.run(args, capture_output=True, text=True, timeout=15)
    if out.returncode != 0 or 'OK' not in out.stdout:
        raise RuntimeError(f'种验证码失败: rc={out.returncode} out={out.stdout} err={out.stderr}')


def cloud_login() -> tuple[str, str]:
    """真登录换 (agent_hasn_id, agent_jwt)。"""
    seed_sms_code(PHONE)
    r = httpx.post(f'{CLOUD}/api/v1/hasn/auth/phone/verify', json={'phone': PHONE, 'code': CODE}, timeout=30)
    r.raise_for_status()
    tokens = r.json().get('agent_tokens') or []
    if not tokens:
        raise RuntimeError(f'账号 {PHONE} 名下没有分身，换个有分身的账号（DOC35_PHONE=…）')
    return tokens[0]['agent_hasn_id'], tokens[0]['access_token']


class McpClient:
    """云端 MCP streamable 最小客户端（Agent JWT 兼容路，见 streamable.py::_authenticate_with_jwt）。"""

    def __init__(self, agent_hasn_id: str, token: str) -> None:
        self._url = f'{CLOUD}/api/v1/mcp/streamable'
        self._headers = {
            'Authorization': f'Bearer {token}',
            'X-HASN-Agent-ID': agent_hasn_id,
            'Content-Type': 'application/json',
            'Accept': 'application/json, text/event-stream',
        }
        self._client = httpx.Client(timeout=120)
        self._id = 0
        self._session: str | None = None

    def _rpc(self, method: str, params: dict | None = None, notify: bool = False) -> dict:
        self._id += 1
        body: dict = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            body['params'] = params
        if not notify:
            body['id'] = self._id
        headers = dict(self._headers)
        if self._session:
            headers['Mcp-Session-Id'] = self._session
        r = self._client.post(self._url, json=body, headers=headers)
        if r.status_code >= 400:
            raise RuntimeError(f'{method} HTTP {r.status_code}: {r.text[:400]}')
        if sid := r.headers.get('mcp-session-id'):
            self._session = sid
        if notify or not r.text.strip():
            return {}
        for line in r.text.splitlines():  # streamable 回 SSE 帧
            if line.startswith('data: '):
                return json.loads(line[6:])
        return r.json()

    def initialize(self) -> None:
        self._rpc(
            'initialize',
            {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'doc35-e2e', 'version': '1.0'}},
        )
        self._rpc('notifications/initialized', {}, notify=True)

    def call_tool(self, name: str, args: dict) -> dict:
        resp = self._rpc('tools/call', {'name': name, 'arguments': args})
        if 'error' in resp:
            raise RuntimeError(f'{name} 报错: {json.dumps(resp["error"], ensure_ascii=False)[:400]}')
        content = (resp.get('result') or {}).get('content') or []
        return json.loads(content[0]['text']) if content else {}


def query_pg(sql: str) -> list[list[str]]:
    """真 PG 查询（本地 15432）。"""
    out = subprocess.run(['psql', *PG_ARGS, '-tAF', '|', '-c', sql], capture_output=True, text=True, timeout=30)
    if out.returncode != 0:
        raise RuntimeError(f'PG 查询失败: {out.stderr[:300]}')
    return [line.split('|') for line in out.stdout.strip().splitlines() if line.strip()]


def run_output_gate(kind: str, resource_kind: str) -> dict:
    """在云端 venv 里跑**真** output_gate.satisfies，喂刚登记的真产物形状。

    产出闸是 doc35 的另一半：登记填对了 kind，闸才比得上。这里不 mock 判定逻辑，
    直接 import 云端共享纯函数（daemon Rust 侧同构实现由 hasn-node 自己的单测钉死）。
    """
    code = f'''
import json
from backend.app.hasn.schema.output_spec import OutputSpec
from backend.app.hasn.service.output_gate import satisfies

# 刚由 register-on-write 真实登记的产物形状（四维度取自 PG 实际读回值）
arts = [{{'kind': {kind!r}, 'resource_kind': {resource_kind!r}}}]
spec = OutputSpec.model_validate

print(json.dumps({{
    'hit_resource_kind': satisfies(spec({{'required': True, 'expects': [{{'resource_kind': {resource_kind!r}}}]}}), arts),
    'hit_artifact_kind': satisfies(spec({{'required': True, 'expects': [{{'artifact_kind': {kind!r}}}]}}), arts),
    'miss_other_app': satisfies(spec({{'required': True, 'expects': [{{'resource_kind': 'knowledge.base'}}]}}), arts),
    'miss_retired_kind': satisfies(spec({{'required': True, 'expects': [{{'resource_kind': 'deck'}}]}}), arts),
    'no_spec': satisfies(None, arts),
    'not_required': satisfies(spec({{'required': False, 'expects': []}}), arts),
    'any_artifact': satisfies(spec({{'required': True, 'expects': []}}), arts),
    'any_artifact_empty': satisfies(spec({{'required': True, 'expects': []}}), []),
}}))
'''
    out = subprocess.run(
        [str(VENV_PYTHON), '-c', code], capture_output=True, text=True, cwd=str(BACKEND_ROOT), timeout=90
    )
    if out.returncode != 0:
        return {'error': (out.stderr or out.stdout)[-600:]}
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {'error': f'解析失败: stdout={out.stdout[-400:]} stderr={out.stderr[-300:]}'}


def main() -> int:
    failures: list[str] = []

    def check(label: str, actual, expected) -> None:
        ok = actual == expected
        print(f'  {"✅" if ok else "❌"} {label}: {actual!r}' + ('' if ok else f' (期望 {expected!r})'))
        if not ok:
            failures.append(f'{label}: 实得 {actual!r}，期望 {expected!r}')

    print(f'=== doc35 E2 全栈活体 E2E（session_id={SESSION_ID}）')
    print(f'[1] 真登录 {PHONE} …')
    agent_id, token = cloud_login()
    print(f'    分身={agent_id}')

    print('[2] 云端 MCP streamable 握手 …')
    mcp = McpClient(agent_id, token)
    mcp.initialize()

    print('[3] 分身真调 hasn.deck.create（带 _hasn_session_id）…')
    created = mcp.call_tool(
        'hasn.deck.create',
        {
            'title': f'doc35 四维度 E2E {SESSION_ID}',
            'topic': 'doc35 产物分类体系验证',
            '_hasn_session_id': SESSION_ID,
        },
    )
    deck_id = created.get('deck_id') or created.get('id') or (created.get('deck') or {}).get('id')
    print(f'    deck_id={deck_id}（云端权威 id）')
    if not deck_id:
        print(f'    ❌ 返回体拿不到 deck_id: {json.dumps(created, ensure_ascii=False)[:400]}')
        return 1

    print('[4] 查真 PG hasn_artifacts 断言四维度 …')
    rows = query_pg(
        "SELECT kind, coalesce(resource_kind,'<NULL>'), coalesce(source_app_id,'<NULL>'), "
        "coalesce(source_kind,'<NULL>'), coalesce(resource_uri,'<NULL>'), coalesce(session_id,'<NULL>') "
        f"FROM hasn_artifacts WHERE session_id = '{SESSION_ID}' ORDER BY created_time DESC LIMIT 1;"
    )
    if not rows:
        print(f'  ❌ hasn_artifacts 无 session_id={SESSION_ID} 的行 —— register-on-write 没登记')
        return 1

    kind, resource_kind, source_app_id, source_kind, resource_uri, session_id = rows[0]
    check('artifact_kind(列名 kind)·怎么打开', kind, 'resource')
    check('resource_kind·是什么', resource_kind, 'deck.presentation')
    check('source_app_id·谁产出', source_app_id, 'deck')
    check('source_kind·怎么来的', source_kind, 'app')
    check('resource_uri·云端权威 id', resource_uri, f'hasn://deck/{deck_id}')
    check('session_id·绑工作会话', session_id, SESSION_ID)

    print('[5] 产出闸（output_gate.satisfies）喂真产物行判定 …')
    gate = run_output_gate(kind, resource_kind)
    if 'error' in gate:
        print(f'  ❌ 产出闸跑失败: {gate["error"][:300]}')
        return 1
    # 语义见 doc35 §0.2：无 spec/required=false 直过；expects 空+required 需任意产物；否则 expects 之间「或」。
    check('闸·expects 命中 resource_kind → 放行', gate['hit_resource_kind'], True)
    check('闸·expects 命中 artifact_kind → 放行', gate['hit_artifact_kind'], True)
    check('闸·expects 是别的应用资源 → 拦', gate['miss_other_app'], False)
    check('闸·expects 是已砍的旧 kind(deck) → 拦', gate['miss_retired_kind'], False)
    check('闸·无 spec → 直过', gate['no_spec'], True)
    check('闸·required=false → 直过', gate['not_required'], True)
    check('闸·expects 空 + required + 有产物 → 过', gate['any_artifact'], True)
    check('闸·expects 空 + required + 无产物 → 拦', gate['any_artifact_empty'], False)

    print()
    if failures:
        print(f'=== ❌ 失败 {len(failures)} 项:')
        for f in failures:
            print(f'  - {f}')
        return 1
    print('=== ✅ doc35 E2 全栈活体 E2E 全绿：四维度 + 云端权威 URI + 会话绑定 + 产出闸八场景')
    return 0


if __name__ == '__main__':
    sys.exit(main())
