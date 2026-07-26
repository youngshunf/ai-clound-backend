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

from backend.app.hasn.model import HasnArtifactContributions, HasnArtifacts
from backend.app.mcp.auth import AgentContext
from backend.app.mcp.artifact_registration import register_app_resource_artifact
from backend.app.mcp.context import (
    clear_current_project_id,
    clear_current_work_session_id,
    set_current_project_id,
    set_current_work_session_id,
)
from backend.app.mcp.tools.project import _h_create
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
    # doc36 U1 收编：这四条以前**绕过公共接缝**直调 service（各自手取 descriptor），于是守卫覆盖不到
    # ——deck/designsystem/plan 三个应用的 register-on-write 长年无守卫。收编进接缝后补上。
    ('deck', 'deck.presentation', 'hasn://deck/', 'resource'),
    ('designsystem', 'designsystem.spec', 'hasn://designsystem/', 'resource'),
    ('plan', 'plan.goal', 'hasn://plan/goals/', 'resource'),
    ('plan', 'plan.plan', 'hasn://plan/plans/', 'resource'),
    # doc38 PJ U3：平台项目自身也是一等资源（`hasn.project.create` register-on-write）。
    # 单资源 manifest（resource_kind=project / uri_domain=project）→ URI 前缀 hasn://project/。
    ('project', 'project', 'hasn://project/', 'resource'),
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


async def _active_rows(
    session, agent_hasn_id: str
) -> list[tuple[HasnArtifacts, HasnArtifactContributions]]:
    """按参与分身读取当前态及其不可变参与记录。"""
    result = await session.execute(
        sa.select(HasnArtifacts, HasnArtifactContributions)
        .join(
            HasnArtifactContributions,
            HasnArtifactContributions.artifact_id == HasnArtifacts.artifact_id,
        )
        .where(
            HasnArtifactContributions.agent_hasn_id == agent_hasn_id,
            HasnArtifacts.status == 'active',
        )
        .order_by(HasnArtifactContributions.id)
    )
    return list(result.all())


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
        artifact, contribution = rows[0]
        assert artifact.resource_uri == f'{uri_prefix}{server_id}', '资源 URI 必须由 manifest 的 uri_domain 派生'
        assert contribution.work_session_id == work_session_id, '参与记录必须绑上工作会话，否则挂不进会话资源栏'
        assert artifact.owner_hasn_id == owner_hasn_id
        # 当前态只保留对象描述，操作上下文只进入 contribution。
        assert artifact.artifact_kind == artifact_kind, 'artifact_kind 只答「怎么打开」——应用资源恒 resource'
        assert artifact.resource_kind == resource_kind, (
            'resource_kind 必须存 descriptor 原值，UI 据它查 registry 取展示名'
        )
        assert artifact.resource_app_id == app_id
        assert contribution.source_app_id == app_id
        assert contribution.source_kind == 'app_write'
    finally:
        clear_current_work_session_id()


async def test_project_id_from_contextvar_lands_on_artifact(pg_session) -> None:
    """doc38 §3.4：分发入口把系统注入的 `_hasn_project_id` 落进 ContextVar 后，接缝**缺省自动取用**
    落 `hasn_artifacts.project_id`——已接应用零改造即自动打标。

    与 `_hasn_session_id` 完全同管道（同一 register-on-write 接缝一次取两个 ContextVar）。这里用
    已接 register-on-write 的 knowledge 应用验证接缝行为，不依赖平台项目应用自身的 resources[] 声明。
    """
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id, owner_hasn_id = f'a_proj_{tag}', f'h_proj_{tag}'
    work_session_id = f'ws_proj_{tag}'
    project_id = str(uuid.uuid4())

    set_current_work_session_id(work_session_id)
    set_current_project_id(project_id)
    try:
        await register_app_resource_artifact(
            pg_session,
            app_id='knowledge',
            resource_kind='knowledge.base',
            server_id=90003,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title=f'项目内产物 {tag}',
            source_tool='hasn.knowledge.create',
        )
        rows = await _active_rows(pg_session, agent_hasn_id)
        assert len(rows) == 1
        _artifact, contribution = rows[0]
        assert str(contribution.project_id) == project_id, '接缝必须把 ContextVar project_id 写入参与记录'
        assert contribution.work_session_id == work_session_id, 'project_id 打标不得影响既有会话参与记录'
    finally:
        clear_current_project_id()
        clear_current_work_session_id()


async def test_project_tool_create_registers_artifact_to_its_new_project(pg_session) -> None:
    """`hasn.project.create` 是项目根资源：即使从旧项目会话发起，也必须自归属新项目。

    这里同时覆盖无项目/旧项目两种危险上下文中的后一种：若写点继续依赖接缝默认
    ContextVar，新项目产物会错误落到旧项目，项目自身的全链路视图就永远看不见它。
    """
    tag = uuid.uuid4().hex[:8]
    old_project_id = str(uuid.uuid4())
    owner = f'h_project_create_{tag}'
    agent = f'a_project_create_{tag}'
    context = AgentContext(
        hasn_id=agent,
        owner_id=1,
        agent_status='active',
        metadata={},
        owner_hasn_id=owner,
        session_uuid=str(uuid.uuid4()),
    )

    set_current_project_id(old_project_id)
    try:
        created = await _h_create(pg_session, context, {'name': '新项目自身产物'})
        rows = await _active_rows(pg_session, agent)
        assert created['uri'] == f"hasn://project/{created['id']}"
        assert len(rows) == 1
        _artifact, contribution = rows[0]
        assert str(contribution.project_id) == created['id']
        assert str(contribution.project_id) != old_project_id
    finally:
        clear_current_project_id()


async def test_project_participation_is_append_only(pg_session) -> None:
    """项目和会话属于参与上下文，后续写入不能覆盖已记录的参与事实。"""
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id, owner_hasn_id = f'a_padv_{tag}', f'h_padv_{tag}'
    project_id = str(uuid.uuid4())

    # 第一次：在项目中写（ContextVar 有 project_id）→ 锁定。
    set_current_project_id(project_id)
    try:
        await register_app_resource_artifact(
            pg_session,
            app_id='knowledge',
            resource_kind='knowledge.base',
            server_id=90004,
            agent_hasn_id=agent_hasn_id,
            owner_hasn_id=owner_hasn_id,
            title='第一版（项目内）',
            source_tool='hasn.knowledge.create',
        )
    finally:
        clear_current_project_id()

    # 第二次：非项目直调改同一资源。项目归属是本次参与上下文，不能覆盖首次参与事实。
    await register_app_resource_artifact(
        pg_session,
        app_id='knowledge',
        resource_kind='knowledge.base',
        server_id=90004,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=owner_hasn_id,
        title='第二版（项目外改稿）',
        source_tool='hasn.knowledge.create',
        action='update',
        dispatch_id=f'dispatch_{tag}_update',
    )
    rows = await _active_rows(pg_session, agent_hasn_id)
    assert len(rows) == 2, '两次不同写入必须各留一条参与记录'
    assert len({artifact.artifact_id for artifact, _contribution in rows}) == 1, '同一资源只保留一条当前态'
    assert str(rows[0][1].project_id) == project_id, '首次项目参与事实不得被后续写入覆盖'
    assert rows[1][1].project_id is None, '非项目写入必须如实记录为无项目参与'
    assert rows[1][1].action == 'update'
    assert rows[1][0].title == '第二版（项目外改稿）', '当前态标题应更新为最新写入'


async def test_repeated_writes_keep_one_current_artifact_and_append_contributions(pg_session) -> None:
    """改稿是常态：一条当前态，且每次不同写入均留下不可变参与记录。"""
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
            action='create' if i == 0 else 'update',
            dispatch_id=f'dispatch_{tag}_{i}',
        )
    rows = await _active_rows(pg_session, agent_hasn_id)
    assert len(rows) == 3, '每次不同写入都必须追加参与记录'
    assert len({artifact.artifact_id for artifact, _contribution in rows}) == 1, '反复写同一资源只能有一条当前态'


async def test_unknown_resource_kind_skips_without_raising(pg_session) -> None:
    """声明缺失 → 跳过登记但**绝不抛**：登记是 best-effort，不能拖垮业务写本身。

    doc36 §3.1：此路径**返 None**（URI 算不出来——不知道 uri_domain），写工具据此省略 `uri` 字段。
    """
    tag = uuid.uuid4().hex[:8]
    agent_hasn_id = f'a_none_{tag}'

    registration = await register_app_resource_artifact(
        pg_session,
        app_id='quant',
        resource_kind='quant.does_not_exist',
        server_id=1,
        agent_hasn_id=agent_hasn_id,
        owner_hasn_id=f'h_none_{tag}',
        title='不该登记',
        source_tool='hasn.quant.test',
    )
    assert registration is None, 'descriptor 解析不出 → 返 None，写工具省略 uri（不许返空串/假 URI）'
    assert await _active_rows(pg_session, agent_hasn_id) == []


@pytest.mark.parametrize(('app_id', 'resource_kind', 'uri_prefix', 'artifact_kind'), ROLLOUT)
async def test_register_returns_resource_uri(pg_session, app_id, resource_kind, uri_prefix, artifact_kind) -> None:
    """doc36 §3.1 D1 核心：接缝必须把算好的 `resource_uri` 交还给写工具。

    以前 URI 在 `record_app_resource_artifact` 里算出来就地扔掉（返回值只有 artifact_id），于是
    **分身写完拿不到能打开的地址**——这是 doc36 要修的根因单点。这里逐应用钉死返回值。
    """
    tag = uuid.uuid4().hex[:8]
    server_id = 90002

    registration = await register_app_resource_artifact(
        pg_session,
        app_id=app_id,
        resource_kind=resource_kind,
        server_id=server_id,
        agent_hasn_id=f'a_uri_{tag}',
        owner_hasn_id=f'h_uri_{tag}',
        title=f'{app_id} 产物 {tag}',
        source_tool=f'hasn.{app_id}.test',
    )
    assert registration is not None, f'{app_id}/{resource_kind} 登记应成功并返回 ArtifactRegistration'
    assert registration.resource_uri == f'{uri_prefix}{server_id}', 'URI 必须由 manifest 的 uri_domain 派生'
    assert registration.artifact_id, '登记成功必须带 artifact_id（审计用）'


async def test_uri_is_built_by_descriptor_single_point() -> None:  # noqa: RUF029  # 模块级 pytestmark 统一 asyncio，纯函数用例跟着写 async 保持一致
    """doc36 §3.1 修订：真正的单点是**拼接函数** `ResourceDescriptor.build_uri`，不止「返回值透传」。

    读路径（`_kb_dict` 等投影）也要产 URI；若只让写路径算好再透传，读路径仍得自己拼一份，单点即破。
    这里钉死 builder 本身的行为，任何人想再手拼 `f'hasn://{...}/{...}'` 都该先看到它。
    """
    from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry

    descriptor = ai_native_app_registry.resource_descriptor('knowledge', 'knowledge.base')
    assert descriptor is not None
    assert descriptor.build_uri(42) == 'hasn://knowledge/kbs/42'
    assert descriptor.build_uri('42') == 'hasn://knowledge/kbs/42', 'str/int 两种 server_id 结果一致'


async def test_merge_resource_uri_puts_uri_into_write_payload() -> None:  # noqa: RUF029  # 同上：模块级 pytestmark 统一 asyncio
    """doc36 §3.2 契约：写工具返回体带 `uri`，登记失败则**省略**该字段。

    这条是 doc36 的核心根因——URI 在登记那一刻就算出来了，然后被原地扔掉，分身写完只拿到一个裸 id。
    所有写工具统一经 `merge_resource_uri` 并进返回体，这里钉死它的两种分支。
    """
    from backend.app.hasn.schema.resource_descriptor import ArtifactRegistration
    from backend.app.mcp.artifact_registration import merge_resource_uri

    registration = ArtifactRegistration(artifact_id='art_1', resource_uri='hasn://knowledge/kbs/7')
    merged = merge_resource_uri({'id': 7, 'name': '库'}, registration)
    assert merged == {'id': 7, 'name': '库', 'uri': 'hasn://knowledge/kbs/7'}

    # 登记返 None（descriptor 解析不出、URI 无从算起）→ 原样返回、省略 uri。
    # 绝不能返空串或假 URI：分身拿到打不开的地址，只会以为是自己用错了。
    payload = {'id': 7, 'name': '库'}
    assert merge_resource_uri(payload, None) == payload
    assert 'uri' not in merge_resource_uri(payload, None)

    # 不就地改入参（写工具的 result 常还要被调用方复用）。
    assert 'uri' not in payload
