"""DSCARD 设计系统「完成发卡 + 产物登记」真实 PG 测试（零 mock）。

福仔 2026-07-14「像 deck 那样」：分身新增/修改设计系统完成后，应像 deck 一样
① 自动发卡到主人主会话（`route_message` 从分身→主人，深链 `hasn://designsystem/{云端权威 id}`）；
② 登记进 `hasn_artifacts`（`hasn://designsystem/{id}`）→ 出现在「工作会话资源栏 / 分身产物 tab」可查看。

与旧 DSFIX-1（notification_service.emit → OwnerLoopback 汇报面）不同，现按 deck 范式两段式：
- `save()` 只**判定并打包完成信号**（`out['completion_card']`），不再内联发卡/落水位；
- `hasn.designsystem.save` 工具在写事务**提交后**独立会话里经 `emit_designsystem_completion_card`
  发卡（`route_message`，`local_id=designsystem_complete:{id}` 幂等）+ 投递成功才回填
  `completed_notified_at`（`mark_completion_notified`，首投失败留空、下次完整 save 自愈补发）。

覆盖：
- `_content_complete` 纯函数：全非空 True，任一空/缺 False；
- 分身完整 save → 透出 `completion_card` 信号，此时不落水位、不发卡（发卡由工具 post-commit）；
- `emit_designsystem_completion_card` → 主会话落 content_type=5 卡，深链=云端权威 id；重复 emit 幂等；
- `mark_completion_notified` → 回填 completed_notified_at（幂等，已标不覆盖）；标记后再完整 save 不再出信号；
- register-on-write：`record_app_resource_artifact`(designsystem descriptor) → hasn_artifacts 行 resource_uri
  `hasn://designsystem/{id}` + session_id + kind=other；
- 分身内容不完整（缺一必填）→ 无信号；owner 本人（human）完整 save → 无信号（仅分身作者触发）。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_agents import HasnAgents
from backend.app.hasn.model.hasn_artifacts import HasnArtifacts
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.model.hasn_messages import HasnMessages
from backend.app.hasn.model.hasn_notifications import HasnNotifications
from backend.app.hasn.service.hasn_artifacts_service import HasnArtifactsService
from backend.app.hasn.service.hasn_sessions_service import (
    _designsystem_resource_descriptor,
    emit_designsystem_completion_card,
)
from backend.app.hasn_designsystem.service.design_system_service import (
    Subject,
    _content_complete,
    design_system_service,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
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


@pytest_asyncio.fixture
async def seeded(session):
    """主人（HasnHumans）+ 其名下分身（HasnAgents.owner_id=owner）——route_message 分身→主人放行前提。"""
    tag = uuid.uuid4().hex[:8]
    uid_owner = 970000 + int(uuid.uuid4().int % 9000)
    owner = f'h_own_{tag}'
    agent = f'a_ds_{tag}'
    session.add_all(
        [
            HasnHumans(
                hasn_id=owner, star_id=f's_{uid_owner}', user_id=uid_owner, nickname=f'主人{tag}', status='active'
            ),
            HasnAgents(
                hasn_id=agent,
                star_id=f'sa_{tag}',
                owner_id=owner,
                display_name=f'设计分身{tag}',
                agent_name=f'ds{tag}',
                status='active',
            ),
        ]
    )
    await session.commit()
    return {'session': session, 'owner': owner, 'agent': agent, 'tag': tag}


def _complete_content() -> dict:
    """一套完整设计系统内容（详情四区块必填字段全非空）。"""
    return {
        'tokens_css': ':root { --bg: #101010; --accent: #2563EB; }',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明\n本设计系统面向 SaaS 后台。',
        'components_html': '<button class="btn">Go</button>',
        'components_manifest_json': {'groups': [{'name': 'buttons', 'items': ['btn']}]},
        'token_contract_report_json': {'summary': {'score': 88, 'grade': 'good', 'recommendRebuild': False}},
    }


async def _designsystem_cards(session, owner: str, agent: str, ds_id: int) -> list[HasnMessages]:
    """主人 ⇄ 分身 主会话里、指向该设计系统的完成卡（content_type=5，deck 同款泛化卡）。"""
    rows = (
        await session.execute(
            select(HasnMessages).where(
                HasnMessages.from_id == agent,
                HasnMessages.to_id == owner,
                HasnMessages.content_type == 5,
            )
        )
    ).scalars().all()
    return [m for m in rows if ((m.content or {}).get('resource') or {}).get('uri') == f'hasn://designsystem/{ds_id}']


async def _center_rows(session, owner: str) -> list[HasnNotifications]:
    """通知中心权威行（新范式走 route_message 会话卡，通知中心应恒无 designsystem 行）。"""
    return (
        await session.execute(
            select(HasnNotifications).where(
                HasnNotifications.target_id == owner,
                HasnNotifications.type == 'designsystem.ready',
            )
        )
    ).scalars().all()


# ==================== 纯函数 ====================


def test_content_complete_pure() -> None:
    """_content_complete：全必填非空 → True；任一空/缺 → False（零造假，只认真实非空）。"""
    assert _content_complete(_complete_content()) is True
    missing = _complete_content()
    del missing['design_md']
    assert _content_complete(missing) is False
    blank = _complete_content()
    blank['components_html'] = '   '
    assert _content_complete(blank) is False
    empty = _complete_content()
    empty['components_manifest_json'] = {}
    assert _content_complete(empty) is False


# ==================== save() 只发信号，不内联发卡/落水位 ====================


async def test_agent_complete_save_emits_signal_only(seeded) -> None:
    """分身写满必填字段 → save 透出 completion_card 信号；此时不落水位、主会话无卡（发卡由工具 post-commit）。"""
    session, owner, agent, tag = seeded['session'], seeded['owner'], seeded['agent'], seeded['tag']
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='已完成设计系统',
        content=_complete_content(),
        score=88,  # 工具从 token_contract_report 摘要透传，进完成卡 summary「· 评分 88」
    )
    ds_id = saved['id']
    # 信号已透出，字段齐全
    card = saved['completion_card']
    assert isinstance(card, dict)
    assert card['design_system_id'] == str(ds_id)
    assert card['title'] == '已完成设计系统'
    assert card['summary'].startswith('已完成设计系统')
    assert '评分 88' in card['summary']
    # save 不再内联落水位（改由工具投递成功后回填）
    assert saved['completed_notified_at'] is None
    # save 本身不发卡，主会话还没有完成卡
    assert await _designsystem_cards(session, owner, agent, ds_id) == []


async def test_agent_incomplete_save_no_signal(seeded) -> None:
    """分身内容不完整（缺一必填）→ 无 completion_card 信号、completed_notified_at 保持 None。"""
    session, owner, agent, tag = seeded['session'], seeded['owner'], seeded['agent'], seeded['tag']
    content = _complete_content()
    del content['components_html']  # 缺组件画廊 → 未完整
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='未完成设计系统',
        content=content,
    )
    assert saved['completion_card'] is None
    assert saved['completed_notified_at'] is None


async def test_owner_complete_save_no_signal(seeded) -> None:
    """owner 本人（human）完整 save → 无信号（仅分身作者触发；owner 自己建不需卡）。"""
    session, owner, tag = seeded['session'], seeded['owner'], seeded['tag']
    saved = await design_system_service.save(
        session,
        subject=Subject.human(owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='主人自建',
        content=_complete_content(),
    )
    assert saved['completion_card'] is None
    assert saved['completed_notified_at'] is None


# ==================== 完成卡投递（deck 同款 route_message，幂等）====================


async def test_emit_completion_card_lands_in_conversation_and_idempotent(seeded) -> None:
    """emit_designsystem_completion_card：主人主会话落一张 content_type=5 卡，深链=云端权威 id；重复 emit 幂等。"""
    session, owner, agent, tag = seeded['session'], seeded['owner'], seeded['agent'], seeded['tag']
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='季度设计规范',
        content=_complete_content(),
    )
    ds_id = saved['id']

    await emit_designsystem_completion_card(
        session, owner_id=owner, agent_id=agent, design_system_id=str(ds_id), title='季度设计规范', summary='第一版做好了'
    )

    cards = await _designsystem_cards(session, owner, agent, ds_id)
    assert len(cards) == 1, f'应恰好落 1 张完成卡，实际 {len(cards)}'
    body = cards[0].content
    assert cards[0].from_id == agent
    # 泛化卡：verb「设计系统」+ 做好了；来源 app=designsystem；深链/主按钮均云端权威 id
    assert body['title'] == '设计系统做好了'
    assert body['source']['id'] == 'designsystem'
    assert body['resource']['uri'] == f'hasn://designsystem/{ds_id}'
    assert body['primary_action']['uri'] == f'hasn://designsystem/{ds_id}'
    assert body['primary_action']['label'] == '打开设计系统'
    assert body['description'] == '第一版做好了'
    # 走会话卡，通知中心恒无 designsystem 权威行
    assert await _center_rows(session, owner) == []

    # 幂等：同 ds_id → 同 local_id(designsystem_complete:{id}) → 不重复发卡
    await emit_designsystem_completion_card(
        session, owner_id=owner, agent_id=agent, design_system_id=str(ds_id), title='季度设计规范'
    )
    cards2 = await _designsystem_cards(session, owner, agent, ds_id)
    assert len(cards2) == 1, f'重复 emit 应幂等不重复发卡，实际 {len(cards2)}'


async def test_mark_completion_notified_idempotent_and_gates_signal(seeded) -> None:
    """mark_completion_notified 回填水位（幂等不覆盖）；标记后再完整 save 不再出信号（快路跳过）。"""
    session, owner, agent, tag = seeded['session'], seeded['owner'], seeded['agent'], seeded['tag']
    first = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='水位测试',
        content=_complete_content(),
    )
    ds_id = first['id']
    assert first['completion_card'] is not None
    assert first['completed_notified_at'] is None

    # 工具投递成功后回填水位
    await design_system_service.mark_completion_notified(session, ds_id)
    saved_row = await design_system_service.get(session, viewer_owner_hasn_id=owner, design_system_id=ds_id)
    marked_at = saved_row['completed_notified_at']
    assert marked_at is not None
    # 幂等：再标不覆盖既有时间戳
    await design_system_service.mark_completion_notified(session, ds_id)
    saved_row2 = await design_system_service.get(session, viewer_owner_hasn_id=owner, design_system_id=ds_id)
    assert saved_row2['completed_notified_at'] == marked_at

    # 标记后同 design_system_id 再完整 save → 水位已落 → 不再出信号（快路跳过，避免重复投递尝试）
    again = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=ds_id,
        slug=f'cc-{tag}',
        name='水位测试改名',
        content=_complete_content(),
    )
    assert again['completion_card'] is None


# ==================== register-on-write 产物登记（工作会话资源栏 / 分身产物 tab）====================


async def test_register_on_write_designsystem_artifact(seeded) -> None:
    """record_app_resource_artifact(designsystem descriptor) → hasn_artifacts 行 resource_uri
    hasn://designsystem/{id} + session_id + kind=other（对齐 deck，工具每次 save 调）。"""
    session, owner, agent, tag = seeded['session'], seeded['owner'], seeded['agent'], seeded['tag']
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='产物登记测试',
        content=_complete_content(),
    )
    ds_id = saved['id']
    sess_id = f'sess_{tag}'

    await HasnArtifactsService.record_app_resource_artifact(
        session,
        descriptor=_designsystem_resource_descriptor(),
        server_id=str(ds_id),
        session_id=sess_id,
        agent_hasn_id=agent,
        owner_hasn_id=owner,
        title='产物登记测试',
        source_tool='hasn.designsystem.save',
    )
    await session.flush()

    rows = (
        await session.execute(
            select(HasnArtifacts).where(
                HasnArtifacts.agent_hasn_id == agent,
                HasnArtifacts.resource_uri == f'hasn://designsystem/{ds_id}',
            )
        )
    ).scalars().all()
    assert len(rows) == 1, f'应恰好登记 1 条设计系统产物，实际 {len(rows)}'
    row = rows[0]
    assert row.session_id == sess_id
    assert row.origin_ref == f'resource:designsystem:{ds_id}'
    assert row.asset_id is None  # 应用资源走 resource_uri 指针，无 asset 本体
    assert row.status == 'active'

    # 幂等：同 (agent, dispatch_id, resource_uri) 再登记不重复
    await HasnArtifactsService.record_app_resource_artifact(
        session,
        descriptor=_designsystem_resource_descriptor(),
        server_id=str(ds_id),
        session_id=sess_id,
        agent_hasn_id=agent,
        owner_hasn_id=owner,
        title='产物登记测试改名',
        source_tool='hasn.designsystem.save',
    )
    await session.flush()
    rows2 = (
        await session.execute(
            select(HasnArtifacts).where(
                HasnArtifacts.agent_hasn_id == agent,
                HasnArtifacts.resource_uri == f'hasn://designsystem/{ds_id}',
            )
        )
    ).scalars().all()
    assert len(rows2) == 1, f'重复登记应幂等，实际 {len(rows2)}'
