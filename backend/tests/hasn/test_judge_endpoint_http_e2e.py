"""J-S1：通用 LLM 裁判端点进程内 HTTP E2E（真实 PG 15432，零 mock）。

最小 app 挂真实 judge 路由，dependency_overrides 注入 owner + 真实 PG 会话；经
ASGITransport 走完整 FastAPI HTTP 栈（依赖注入 + 统一信封 + errors→HTTP 状态码），
覆盖 service 层测不到的路由外壳/信封漂移。事务末尾回滚不污染库。需 export DATABASE_PORT=15432。

覆盖（对齐清单 J-S1-4）：
- 未知 kind → 422；termination/disclosure 入参超限 → 422（纵深防御，不触 LLM）。
- 无 JWT → 401（真实 auth 依赖运行）。
- owner 缺 new-api 凭据 → 503（LLM 调用前 fail，不触网关）。
- PDC fast+main+pool 全空 → 503（直测 _resolve_model_chain，真实空配置）。
- 契约×2 kind（合法 200 + 落库 judge_kind/owner 正确）infra-gated：
  仅当 env JUDGE_LIVE_OWNER 指向 dev 库真实有 new-api 凭据的 owner 且网关可达才跑，否则 skip 并标注。
- 判定语义质量不在此（归 J-S5 评测集）。
"""
from __future__ import annotations

import os
import uuid

from types import SimpleNamespace

import httpx
import pytest
import pytest_asyncio

from fastapi import FastAPI
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool
from starlette_context.middleware import ContextMiddleware
from starlette_context.plugins import RequestIdPlugin

from backend.app.hasn.api.v1.app.judge import router as judge_router
from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_judge_verdict import HasnJudgeVerdict
from backend.app.hasn.service.hasn_auth import hasn_auth
from backend.app.hasn.service.judge_service import judge_service
from backend.common.exception import errors
from backend.common.exception.exception_handler import register_exception
from backend.common.response.response_code import StandardResponseCode
from backend.common.security.jwt import DependsJwtAuth
from backend.database.db import SQLALCHEMY_DATABASE_URL, get_db, get_db_transaction

pytestmark = pytest.mark.asyncio

_APP = FastAPI()
_APP.include_router(judge_router, prefix='/api/v1/hasn/app', tags=['HASN 用户端'])
register_exception(_APP)  # errors.RequestError(code=422/503) → 真实 HTTP 状态 + 信封
_APP.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])


def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _term_url(kind: str = 'termination') -> str:
    return f'/api/v1/hasn/app/judge/{kind}'


@pytest_asyncio.fixture
async def e2e():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()

    uid_owner = 960000 + int(uuid.uuid4().int % 9000)
    owner = f'h_own_{_uid()}'
    my_agent = f'a_mine_{_uid()}'
    peer = f'h_peer_{_uid()}'
    session.add_all([
        HasnHumans(hasn_id=owner, star_id=f's_{uid_owner}', user_id=uid_owner, nickname='Owner', status='active'),
        HasnAgents(
            hasn_id=my_agent, star_id=f'sa_{_uid()}', owner_id=owner,
            display_name='我的分身', agent_name='mine', status='active',
        ),
        HasnHumans(hasn_id=peer, star_id=f's_{uid_owner + 1}', user_id=uid_owner + 1, nickname='Peer', status='active'),
    ])
    await session.flush()

    current = {'hasn_id': owner, 'star_id': f's_{uid_owner}', 'user_id': uid_owner, 'auth_type': 'jwt'}

    async def _yield_session():
        yield session

    async def _auth_inject():
        return current

    _APP.dependency_overrides[get_db] = _yield_session
    _APP.dependency_overrides[get_db_transaction] = _yield_session
    _APP.dependency_overrides[DependsJwtAuth.dependency] = lambda: 'e2e-token'
    _APP.dependency_overrides[hasn_auth] = _auth_inject

    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=_APP), base_url='http://e2e')
    try:
        yield SimpleNamespace(client=client, session=session, owner=owner, my_agent=my_agent, peer=peer)
    finally:
        await client.aclose()
        _APP.dependency_overrides.clear()
        await session.rollback()
        await session.close()
        await engine.dispose()


def _valid_termination_payload() -> dict:
    return {
        'transcript': [
            {'speaker': 'self', 'text': '这个方案我看完了，同意按你说的改。'},
            {'speaker': 'peer', 'text': '好的，谢谢，那我这边就先这样。'},
        ],
        'turns': 2,
    }


def _valid_disclosure_payload() -> dict:
    return {
        'text': '我主人这周有空，可以约周三下午聊聊合作。',
        'context': ['对方：你们最近方便对接吗？'],
        'peer': {'trust_level': 3, 'relation_type': 'social', 'is_agent': False},
        'l1_hits': [],
    }


# ── 422：未知 kind（spec 查表即拒，不触 LLM）──
async def test_unknown_kind_returns_422(e2e) -> None:
    r = await e2e.client.post(_term_url('mystery'), json={
        'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
        'conversation_ref': 'conv_x', 'payload': {},
    })
    assert r.status_code == 422, r.text


# ── 422：termination transcript 超 60 条（纵深防御）──
async def test_termination_over_limit_422(e2e) -> None:
    payload = {'transcript': [{'speaker': 'self', 'text': 'x'} for _ in range(61)], 'turns': 61}
    r = await e2e.client.post(_term_url('termination'), json={
        'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
        'conversation_ref': 'conv_x', 'payload': payload,
    })
    assert r.status_code == 422, r.text


# ── 422：disclosure trust_level 越界 ──
async def test_disclosure_over_limit_422(e2e) -> None:
    bad = _valid_disclosure_payload()
    bad['peer']['trust_level'] = 9  # 非法：0..5
    r = await e2e.client.post(_term_url('disclosure'), json={
        'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
        'conversation_ref': 'conv_x', 'payload': bad,
    })
    assert r.status_code == 422, r.text


# ── 422：disclosure text 超 2000 字 ──
async def test_disclosure_text_too_long_422(e2e) -> None:
    bad = _valid_disclosure_payload()
    bad['text'] = 'x' * 2001
    r = await e2e.client.post(_term_url('disclosure'), json={
        'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
        'conversation_ref': 'conv_x', 'payload': bad,
    })
    assert r.status_code == 422, r.text


# ── 401：无 JWT（真实 auth 依赖运行，无 override）──
async def test_missing_jwt_401() -> None:
    bare = FastAPI()
    bare.include_router(judge_router, prefix='/api/v1/hasn/app')
    register_exception(bare)
    bare.add_middleware(ContextMiddleware, plugins=[RequestIdPlugin(validate=False)])
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=bare), base_url='http://e2e') as c:
        r = await c.post(_term_url('termination'), json={
            'agent_hasn_id': 'a_x', 'peer_hasn_id': 'h_x',
            'conversation_ref': 'c', 'payload': _valid_termination_payload(),
        })
    assert r.status_code == 401, r.text


# ── 503：owner 缺 new-api 凭据（合法入参过校验后，取 owner key 失败）──
async def test_owner_missing_newapi_creds_503(e2e) -> None:
    r = await e2e.client.post(_term_url('disclosure'), json={
        'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
        'conversation_ref': 'conv_x', 'payload': _valid_disclosure_payload(),
    })
    # seed 的随机 user_id 无 new-api 映射 → get_api_key NotFoundError → 503
    assert r.status_code == 503, r.text


# ── 503/正路：PDC 模型链解析（真实空配置 → 503；已配 → 非空链，链首=fast/main）──
async def test_pdc_model_chain_resolution(e2e) -> None:
    from backend.app.hasn.service.platform_default_config_service import platform_default_config_service

    config, _rev = await platform_default_config_service.get_effective_config(e2e.session)
    rt = config.agent_runtime
    fast = (rt.models.fast or '').strip()
    main = (rt.models.main or '').strip()
    if not fast and not main:
        # 平台默认未配裁判模型 → 必须 503（不静默放行）
        with pytest.raises(errors.RequestError) as ei:
            await judge_service._resolve_model_chain(e2e.session)
        assert ei.value.code == StandardResponseCode.HTTP_503
    else:
        chain = await judge_service._resolve_model_chain(e2e.session)
        assert chain and chain[0] in {fast, main}


# ── 契约×2 kind + 落库 + owner 计费归属（infra-gated：需真实 owner 凭据 + 网关可达）──
@pytest.mark.skipif(
    not os.getenv('JUDGE_LIVE_OWNER'),
    reason='infra-gated：设 JUDGE_LIVE_OWNER=<dev库有new-api凭据的owner hasn_id> 才跑真实 LLM 契约',
)
@pytest.mark.parametrize('kind,payload_fn,verdict_key', [
    ('termination', _valid_termination_payload, 'should_end'),
    ('disclosure', _valid_disclosure_payload, 'allow'),
])
async def test_contract_persists_when_llm_reachable(e2e, kind, payload_fn, verdict_key) -> None:
    live_owner = os.environ['JUDGE_LIVE_OWNER']
    # 直接用真实 owner 身份（其在 dev 库有 new-api 凭据 + PDC 已配裁判模型）
    async def _auth_live():
        return {'hasn_id': live_owner, 'auth_type': 'jwt'}

    _APP.dependency_overrides[hasn_auth] = _auth_live
    try:
        r = await e2e.client.post(_term_url(kind), json={
            'agent_hasn_id': e2e.my_agent, 'peer_hasn_id': e2e.peer,
            'conversation_ref': f'conv_{_uid()}', 'payload': payload_fn(),
        })
    finally:
        _APP.dependency_overrides[hasn_auth] = lambda: {'hasn_id': e2e.owner}
    if r.status_code == 503:
        pytest.skip(f'裁判 LLM 网关不可达/未配模型，跳过契约: {r.text}')
    assert r.status_code == 200, r.text
    body = r.json()
    assert body['code'] == 200, body  # 统一信封
    assert isinstance(body['data'].get(verdict_key), bool)

    # 落库核实：judge_kind + owner 正确
    await e2e.session.flush()
    row = (
        await e2e.session.execute(
            select(HasnJudgeVerdict)
            .where(HasnJudgeVerdict.owner_hasn_id == live_owner, HasnJudgeVerdict.judge_kind == kind)
            .order_by(HasnJudgeVerdict.id.desc()).limit(1)
        )
    ).scalar_one_or_none()
    assert row is not None
    assert row.judge_kind == kind
    assert row.model  # 命中模型已记
    assert verdict_key in row.verdict_json
