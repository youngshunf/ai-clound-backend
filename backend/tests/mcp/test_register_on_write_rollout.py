"""register-on-write 横向铺开守卫（真实本地 PostgreSQL，零 mock）——doc31 产物自动登记铁律。

守公共接缝 `backend/app/mcp/artifact_registration.py`（两条分身工具面共用）的三条不变量：

1. **每个已接应用都能解析出 descriptor 并把产物登记落库**。最易漏的是 manifest 少声明 `resources[]`
   或写错 `resource_kind` —— 那时登记会静默跳过（best-effort 只 warn），分身照样干活、主人却看不到产物。
   这里逐个应用钉死「descriptor 在 + URI 由 uri_domain 派生 + 行落库 + 绑上工作会话」。
2. **幂等**：分身改稿是常态，同一资源反复写只留一条 active 行，不许刷屏产物列表。
3. **best-effort**：声明缺失只跳过、绝不抛——登记常与业务写同事务，抛出会连累正事落库。

只测公共接缝本身，不驱动各应用 service（那属各应用自己的测试）——接缝对了，写点接上就对。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.app.mcp.context import clear_current_work_session_id, set_current_work_session_id
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

# 已接 register-on-write 的应用 → (app_id, resource_kind, 期望 URI 前缀, 期望 artifact_kind)。
# 新接应用请在此补一行——漏了就没有守卫，等于回到「声明了 resources[] 却没人登记」的老账。
#
# 第四列 `artifact_kind` 是 doc35 A4 补的：此前三元组**一个 kind 都没钉**，于是 kind 长年随便漂
# （deck/dataset/webpage/other 混着来），谁也没红。应用资源恒 'resource'，明写出来是为了「某天有人
# 把它改回 'dataset' 就当场红」。
ROLLOUT = [
    ('knowledge', 'knowledge.base', 'hasn://knowledge/kbs/', 'resource'),
    ('knowledge', 'knowledge.document', 'hasn://knowledge/documents/', 'resource'),
    ('community', 'community.post', 'hasn://community/posts/', 'resource'),
    ('community', 'community.article', 'hasn://community/articles/', 'resource'),
    ('creator', 'creator.project', 'hasn://creator/projects/', 'resource'),
    ('growth', 'growth.customer', 'hasn://growth/customers/', 'resource'),
    ('quant', 'quant.strategy', 'hasn://quant/strategies/', 'resource'),
    ('quant', 'quant.backtest', 'hasn://quant/backtests/', 'resource'),
    ('studio', 'studio.project', 'hasn://studio/projects/', 'resource'),
]


@pytest_asyncio.fixture
async def pg_session():
    """真实本地 PG AsyncSession：flush 不 commit，结束 rollback（PG 侧不留残留）。"""
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


async def _active_rows(session, agent_hasn_id: str) -> list[HasnArtifacts]:
    result = await session.execute(
        sa.select(HasnArtifacts).where(
            HasnArtifacts.agent_hasn_id == agent_hasn_id,
            HasnArtifacts.status == 'active',
        )
    )
    return list(result.scalars().all())


@pytest.mark.parametrize(('app_id', 'resource_kind', 'uri_prefix', 'artifact_kind'), ROLLOUT)
async def test_register_lands_artifact_bound_to_session(
    pg_session, app_id, resource_kind, uri_prefix, artifact_kind
) -> None:
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id, owner_hasn_id = f'a_row_{tag}', f'h_row_{tag}'
    server_id = 90001
    work_session_id = f'ws_row_{tag}'

    set_current_work_session_id(work_session_id)
    try:
        await register_app_resource_artifact(
            pg_session,
            app_id=app_id,
            resource_kind=resource_kind,
            server_id=server_id,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title=f'{app_id} 产物 {tag}',
            source_tool=f'hasn.{app_id}.test',
        )
        rows = await _active_rows(pg_session, agent_hasn_id)
        assert len(rows) == 1, f'{app_id}/{resource_kind} 未登记——多半是 manifest 缺 resources[] 声明或 kind 写错'
        assert rows[0].resource_uri == f'{uri_prefix}{server_id}', '资源 URI 必须由 manifest 的 uri_domain 派生'
        assert rows[0].session_id == work_session_id, '产物必须绑上工作会话，否则挂不进会话资源栏'
        assert rows[0].owner_hasn_id == owner_hasn_id
        # doc35 三维度：kind 只答「怎么打开」、resource_kind 答「是什么」、
        # source_app_id 答「哪个应用」、source_kind 答「怎么来的」。四者各就各位才算登记对。
        assert rows[0].kind == artifact_kind, 'artifact_kind 只答「怎么打开」——应用资源恒 resource'
        assert rows[0].resource_kind == resource_kind, 'resource_kind 必须存 descriptor 原值，UI 据它查 registry 取展示名'
        assert rows[0].source_app_id == app_id
        assert rows[0].source_kind == 'app', '应用产出的资源，来源恒为 app（旧硬编码 tool_output 是垃圾桶）'
    finally:
        clear_current_work_session_id()


async def test_repeated_writes_stay_single_active_row(pg_session) -> None:
    """改稿是常态：同一资源写三次只能留一条 active 产物行。"""
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id, owner_hasn_id = f'a_idem_{tag}', f'h_idem_{tag}'

    clear_current_work_session_id()
    for i in range(3):
        await register_app_resource_artifact(
            pg_session,
            app_id='quant',
            resource_kind='quant.strategy',
            server_id=4242,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title=f'第 {i + 1} 版策略',
            source_tool='hasn.quant.save_strategy',
        )
    rows = await _active_rows(pg_session, agent_hasn_id)
    assert len(rows) == 1, '反复写同一资源只能有一条 active 行（幂等键 = agent + dispatch_id + resource_uri）'


async def test_unknown_resource_kind_skips_without_raising(pg_session) -> None:
    """声明缺失 → 跳过登记但**绝不抛**：登记是 best-effort，不能拖垮业务写本身。"""
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id = f'a_none_{tag}'

    await register_app_resource_artifact(
        pg_session,
        app_id='quant',
        resource_kind='quant.does_not_exist',
        server_id=1,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=f'h_none_{tag}',
        title='不该登记',
        source_tool='hasn.quant.test',
    )
    assert await _active_rows(pg_session, agent_hasn_id) == []
