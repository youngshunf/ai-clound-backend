"""DSFIX-1 设计系统「完成发卡」真实 PG 测试（零 mock）。

福仔铁律（2026-07-04）：设计系统「不要用自动完成」——分身写满设计系统**必填字段**（详情页四区块
所需内容）后，云端 save 判定首次完整且作者是分身 → 发一次「设计系统已完成·查看」卡给主人，
深链 hasn://designsystem/{云端权威 id}。发卡时机 = 必填字段齐了，不是 runtime 自动完成。

覆盖：
- 分身完整 save → 发完成卡（type=designsystem.ready，source.kind=agent，link=/designsystem/{云端id}）
  + completed_notified_at 落幂等水位 + 卡片真 fanout（delivery.card_message_id）；
- 分身内容不完整（缺一必填）→ 不发卡、completed_notified_at 保持 None；
- 幂等：再 save 一次完整内容 → 不发第二张卡（仍只一张 designsystem.ready）；
- owner 本人（human）完整 save → 不发完成卡（仅分身作者触发）；
- _content_complete 纯函数：全非空为 True，任一空/缺为 False。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.model.hasn_notifications import HasnNotifications
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


async def _ready_notifs(session, owner: str, ds_id: int) -> list[HasnNotifications]:
    rows = (
        await session.execute(
            select(HasnNotifications).where(
                HasnNotifications.target_id == owner,
                HasnNotifications.type == 'designsystem.ready',
            )
        )
    ).scalars().all()
    return [n for n in rows if (n.data or {}).get('target', {}).get('id') == str(ds_id)]


def test_content_complete_pure() -> None:
    """_content_complete：全必填非空 → True；任一空/缺 → False（零造假，只认真实非空）。"""
    assert _content_complete(_complete_content()) is True
    # 缺一必填（design_md）→ 未完整
    missing = _complete_content()
    del missing['design_md']
    assert _content_complete(missing) is False
    # 空串必填 → 未完整
    blank = _complete_content()
    blank['components_html'] = '   '
    assert _content_complete(blank) is False
    # 空结构必填 → 未完整
    empty = _complete_content()
    empty['components_manifest_json'] = {}
    assert _content_complete(empty) is False


async def test_agent_complete_save_emits_ready_card(session) -> None:
    """分身写满必填字段 → 发「设计系统已完成」卡 + 幂等水位落地 + 深链用云端权威 id。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = f'a_ds_{tag}'
    saved = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='已完成设计系统',
        content=_complete_content(),
    )
    ds_id = saved['id']
    # 幂等水位已落（首次完整）
    assert saved['completed_notified_at'] is not None

    notifs = await _ready_notifs(session, owner, ds_id)
    assert len(notifs) == 1
    n = notifs[0]
    assert n.category == 'agent'
    assert (n.source or {}).get('kind') == 'agent'
    assert (n.source or {}).get('id') == agent
    # 深链 = 相对 /designsystem/{云端权威 id}（carrier 提升为 hasn://designsystem/{id}）
    assert (n.data or {}).get('link') == f'/designsystem/{ds_id}'
    assert (n.data or {}).get('target', {}).get('id') == str(ds_id)
    # 卡片真 fanout 落「主人 ⇄ 分身」会话（delivery 回指 card_message_id）
    assert (n.delivery or {}).get('card_message_id') is not None


async def test_agent_incomplete_save_no_card(session) -> None:
    """分身内容不完整（缺一必填）→ 不发卡、completed_notified_at 保持 None（详情仍会空是预期）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = f'a_ds_{tag}'
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
    assert saved['completed_notified_at'] is None
    assert await _ready_notifs(session, owner, saved['id']) == []


async def test_ready_card_idempotent(session) -> None:
    """再 save 一次完整内容 → 不发第二张卡（completed_notified_at 幂等水位挡住）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    agent = f'a_ds_{tag}'
    first = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='幂等测试',
        content=_complete_content(),
    )
    ds_id = first['id']
    assert first['completed_notified_at'] is not None

    # 同一套再 save 一版完整内容（同 design_system_id）→ 不应再发卡
    again = await design_system_service.save(
        session,
        subject=Subject.agent(agent, owner),
        design_system_id=ds_id,
        slug=f'cc-{tag}',
        name='幂等测试改名',
        content=_complete_content(),
    )
    # 水位不变（仍是首次那次的时间）
    assert again['completed_notified_at'] == first['completed_notified_at']
    assert len(await _ready_notifs(session, owner, ds_id)) == 1


async def test_owner_complete_save_no_card(session) -> None:
    """owner 本人（human）完整 save → 不发完成卡（仅分身作者触发；owner 自己建不需卡）。"""
    tag = uuid.uuid4().hex[:8]
    owner = f'h_owner_{tag}'
    saved = await design_system_service.save(
        session,
        subject=Subject.human(owner),
        design_system_id=None,
        slug=f'cc-{tag}',
        name='主人自建',
        content=_complete_content(),
    )
    assert saved['completed_notified_at'] is None
    assert await _ready_notifs(session, owner, saved['id']) == []
