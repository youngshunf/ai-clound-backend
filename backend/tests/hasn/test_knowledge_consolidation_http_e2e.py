"""知识库实例/凭据收编 P6 真实 HTTP E2E（真实 PostgreSQL，零 mock，删旧表后）。

实施 14-AI-Native应用平台/实施/03 P6。验证「旧 hasn_ragflow_* 两表已 DROP」后，
云端知识库控制面仍经统一应用平台底座 hasn_app_instance + hasn_app_credential 正确读写，
且对 daemon/webui 的响应字段契约不变（收编对调用方透明）：

  - GET /knowledge/credentials：personal 工作空间经 instance_resolver 解析**公共实例**
    （读 hasn_app_instance，删表后首开知识库页仍 200 出实例 = §自愈分析的控制面侧）。
  - 凭据读取：经 hasn_app_credential 读回；RAGFlow 私有字段（ragflow_user_id/tenant）
    从 config 取；active 凭据解密下发 api_key（零 fake，仅 active 给 key）。
  - 企业实例 save/get：写/读 hasn_app_instance(scope=enterprise)；public_pem/embd/llm 下沉 config；
    admin key 经 key_encryption 加密存 credential_ref。
  - owner 隔离：A 的凭据不串到 B。
  - 鉴权：非企业 owner/admin → 403（收编未削弱授权）。
  - refresh（已有 active 凭据分支）：跳过 provision 直接回读（不依赖真实 RAGFlow）。

注：真实 RAGFlow provision/检索 + restricted grant + agent search 命中/denied 403 全链路
依赖 RAGFlow:18082 + 隔离 daemon，属 infra-gated（见 rf_full_stack_runner.py），不在本进程内 E2E。

事实源：docs/hasn-node设计文档/14-AI-Native应用平台/实施/03-知识库实例与凭据收编迁移实施.md §P6。
"""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.knowledge import router as knowledge_router
from backend.app.hasn.model.hasn_app_credential import HasnAppCredential
from backend.app.hasn.model.hasn_app_instance import HasnAppInstance
from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.llm.core.encryption import key_encryption
from backend.common.exception.exception_handler import register_exception
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.add_middleware(ContextMiddleware, plugins=(RequestIdPlugin(),))
register_exception(_APP)
_APP.include_router(knowledge_router, prefix='/api/v1/hasn/app')

_CRED = '/api/v1/hasn/app/knowledge/credentials'


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _new_user_id() -> int:
    return 980_000_000 + int(uuid.uuid4().int % 19_000_000)


@pytest_asyncio.fixture
async def env():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    # 收编前置：必须已有一条 active 公共知识库实例（迁移/seed 落库）。否则解析不到实例，
    # 本套 E2E 无意义 → 跳过（而非假绿）。
    public = (
        await session.execute(
            select(HasnAppInstance).where(
                HasnAppInstance.app_id == 'knowledge',
                HasnAppInstance.scope == 'public',
                HasnAppInstance.status == 'active',
            )
        )
    ).scalars().first()
    if public is None:
        await session.close()
        await engine.dispose()
        pytest.skip('无 active 公共知识库实例（请先跑 P2 迁移或 seed_local_ragflow.py）')

    auth_state = {'user_id': _new_user_id()}

    async def _yield_session():
        yield session

    async def _auth_inject(request: Request):
        request.scope['user'] = SimpleNamespace(id=auth_state['user_id'])
        request.scope['auth'] = ['authenticated']
        return 'e2e-token'

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(
            client=client, session=session, auth_state=auth_state, public_instance_id=public.id
        )
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _data(resp: httpx.Response):
    assert resp.status_code == 200, f'{resp.request.method} {resp.request.url} -> {resp.status_code}: {resp.text}'
    body = resp.json()
    assert body.get('code') == 200, f'统一信封非 200: {body}'
    return body['data']


async def test_get_credentials_resolves_public_instance_post_drop(env) -> None:
    """删旧表后：personal 工作空间 GET 凭据 → resolver 经 hasn_app_instance 解析公共实例，
    无凭据时 status=pending + 实例非空（控制面侧「首开知识库页仍出实例」）。"""
    data = _data(await env.client.get(_CRED))
    assert data['status'] == 'pending', '新用户无凭据 → pending'
    assert data['credential'] is None, '零 fake：无凭据即 None'
    inst = data.get('instance')
    assert inst is not None, '应解析到公共实例（读 hasn_app_instance，非已删的 hasn_ragflow_instance）'
    assert inst['scope'] == 'public'
    assert inst['id'] == env.public_instance_id
    assert inst['url'], '实例 endpoint 不应为空'


async def test_get_credentials_reads_seeded_credential_via_config_demotion(env) -> None:
    """凭据读路径：经 hasn_app_credential 读回；ragflow_user_id/tenant 从 config 取；active 解密 api_key。"""
    s, c = env.session, env.client
    # 先 GET 拿到解析出的公共实例 id（不硬编码）
    inst_id = _data(await c.get(_CRED))['instance']['id']

    plaintext_key = f'tenant-key-{_uid()}'
    s.add(
        HasnAppCredential(
            app_id='knowledge',
            user_id=env.auth_state['user_id'],
            app_instance_id=inst_id,
            credential_ref=key_encryption.encrypt(plaintext_key),
            status='active',
            config={'ragflow_user_id': 'rf-user-x', 'ragflow_tenant_id': 'rf-tenant-y'},
        )
    )
    await s.flush()

    data = _data(await c.get(_CRED))
    assert data['status'] == 'active'
    cred = data['credential']
    assert cred is not None
    assert cred['instance_id'] == inst_id, '凭据字段 instance_id 仍指应用实例（契约不变）'
    assert cred['ragflow_user_id'] == 'rf-user-x', 'RAGFlow 私有字段从 config 下沉读出'
    assert cred['ragflow_tenant_id'] == 'rf-tenant-y'
    assert cred['api_key'] == plaintext_key, 'active 凭据解密下发明文 key'
    assert cred['api_key_encrypted'] == 'stored'


async def test_refresh_with_active_credential_skips_provision(env) -> None:
    """refresh 已有 active 凭据分支：不触发 provision（不依赖真实 RAGFlow），直接回读 active。"""
    s, c = env.session, env.client
    inst_id = _data(await c.get(_CRED))['instance']['id']
    s.add(
        HasnAppCredential(
            app_id='knowledge',
            user_id=env.auth_state['user_id'],
            app_instance_id=inst_id,
            credential_ref=key_encryption.encrypt(f'k-{_uid()}'),
            status='active',
            config={'ragflow_user_id': 'u', 'ragflow_tenant_id': 't'},
        )
    )
    await s.flush()

    data = _data(await c.post(f'{_CRED}/refresh'))
    assert data['status'] == 'active', 'active 凭据 refresh 直接回读（跳过 provision）'
    assert data['credential']['instance_id'] == inst_id


async def test_save_and_get_enterprise_instance_via_config_demotion(env) -> None:
    """企业实例 save/get：写/读 hasn_app_instance(scope=enterprise)；public_pem/embd/llm 下沉 config；
    admin key 经 key_encryption 存 credential_ref。"""
    s, c = env.session, env.client
    user_id = env.auth_state['user_id']
    ent = HasnEnterprise(name=f'测试企业{_uid()}', slug=f'ent-{_uid()}', owner_user_id=user_id)
    s.add(ent)
    await s.flush()

    url = 'http://10.0.0.9:9380'
    admin_key = f'admin-{_uid()}'
    pem = '-----BEGIN PUBLIC KEY-----\nMOCKPEM\n-----END PUBLIC KEY-----'
    saved = _data(
        await c.put(
            f'/api/v1/hasn/app/knowledge/enterprise/{ent.id}',
            json={
                'url': url,
                'admin_api_key': admin_key,
                'public_pem': pem,
                'default_embd_id': 'bge-x',
                'default_llm_id': 'deepseek-x',
            },
        )
    )
    assert saved['scope'] == 'enterprise'
    assert saved['enterprise_id'] == ent.id
    assert saved['url'] == url
    assert saved['public_pem'] == pem
    assert saved['default_embd_id'] == 'bge-x'
    assert saved['default_llm_id'] == 'deepseek-x'
    assert saved['admin_api_key_encrypted'] == 'stored', 'admin key 不回明文'
    assert saved['status'] == 'active'

    # 底层确实落到 hasn_app_instance(scope=enterprise)，私有字段在 config，admin key 可解密
    row = (
        await s.execute(
            select(HasnAppInstance).where(
                HasnAppInstance.app_id == 'knowledge',
                HasnAppInstance.scope == 'enterprise',
                HasnAppInstance.enterprise_id == ent.id,
            )
        )
    ).scalars().first()
    assert row is not None, '应写入 hasn_app_instance（非已删的 hasn_ragflow_instance）'
    assert row.config.get('public_pem') == pem and row.config.get('default_embd_id') == 'bge-x'
    assert key_encryption.decrypt(row.credential_ref) == admin_key

    # GET 读回一致
    got = _data(await c.get(f'/api/v1/hasn/app/knowledge/enterprise/{ent.id}'))
    assert got['url'] == url and got['public_pem'] == pem and got['scope'] == 'enterprise'


async def test_owner_isolation_credentials(env) -> None:
    """A 的凭据不串到 B：B GET → credential=None。"""
    s, c = env.session, env.client
    inst_id = _data(await c.get(_CRED))['instance']['id']
    s.add(
        HasnAppCredential(
            app_id='knowledge',
            user_id=env.auth_state['user_id'],
            app_instance_id=inst_id,
            credential_ref=key_encryption.encrypt('a-key'),
            status='active',
            config={'ragflow_user_id': 'a', 'ragflow_tenant_id': 'a'},
        )
    )
    await s.flush()
    assert _data(await c.get(_CRED))['status'] == 'active', 'A 自己看得到'

    # 切到 B（不同 user_id，无凭据）
    env.auth_state['user_id'] = _new_user_id()
    data_b = _data(await c.get(_CRED))
    assert data_b['credential'] is None, 'B 不应看到 A 的凭据（owner 隔离）'
    assert data_b['status'] == 'pending'


async def test_enterprise_admin_gate_forbids_non_owner(env) -> None:
    """非企业 owner/admin → 企业实例 save/get 403（收编未削弱授权）。"""
    s, c = env.session, env.client
    foreign_owner_id = _new_user_id()
    ent = HasnEnterprise(name=f'他企业{_uid()}', slug=f'fent-{_uid()}', owner_user_id=foreign_owner_id)
    s.add(ent)
    await s.flush()
    # 当前用户既非 owner 也无 approved 管理成员
    assert (await c.get(f'/api/v1/hasn/app/knowledge/enterprise/{ent.id}')).status_code == 403
    resp = await c.put(
        f'/api/v1/hasn/app/knowledge/enterprise/{ent.id}',
        json={'url': 'http://x', 'admin_api_key': 'k', 'public_pem': 'p'},
    )
    assert resp.status_code == 403, f'非管理员应 403，实际 {resp.status_code}: {resp.text}'
