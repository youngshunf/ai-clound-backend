"""DS-P9 设计系统分享 / 协作分身改版（D6）真实 PG 测试（零 mock）。

覆盖 P9 验收：
- 分享给他人（viewer/editor）真生效：可见域并入、可读、可撤销；
- 协作分身经 editor 改 tokens → 出「待确认版」（不动 owner 当前态）→ owner 采用落版 / 回滚；
- set_current_revision owner 专属裁决（即便协作方有 editor 也不能裁决）；
- 协作分身解绑；share 拒绝授予 manager。

直接打真实本地 PostgreSQL（端口 15432）；不可达则 skip。uuid tag 隔离测试行。
"""

from __future__ import annotations

import uuid

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_designsystem.service.design_system_service import Subject, design_system_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield sess
    finally:
        await sess.rollback()
        await sess.close()
        await engine.dispose()


def _content(bg: str, *, score: int, grade: str) -> dict:
    """一版内容：报告写进 summary（set_current_revision 从 summary 回填评分）。"""
    return {
        'tokens_css': f':root {{ --bg: {bg}; }}',
        'design_tokens_json': {'schemaVersion': 1, 'tokens': []},
        'tailwind_css': '@theme {}',
        'design_md': '# 设计说明',
        'components_html': '<button>Go</button>',
        'components_manifest_json': {'groups': []},
        'token_contract_report_json': {'summary': {'score': score, 'grade': grade, 'recommendRebuild': False}},
    }


async def test_share_visible_readable_and_revoke(session) -> None:
    """A 私有 → B 不可见/不可读；A 分享 viewer → B 可见+可读；撤销 → 重回不可见。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b_id = f'h_b_{tag}'

    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'sh-{tag}', name='A 私有',
        content=_content('#101010', score=70, grade='fair'),
    )
    ds_id = saved['id']

    # 初始：B 不可见、不可读
    b_list = await design_system_service.list_visible(session, viewer_owner_hasn_id=b_id)
    assert all(it['id'] != ds_id for it in b_list['items'])
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=b_id)

    # A 分享 viewer 给 B
    await design_system_service.share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id,
        grantee_type='human', grantee_id=b_id, permission='viewer',
    )
    shares = await design_system_service.list_shares(session, design_system_id=ds_id, owner_hasn_id=a.hasn_id)
    assert shares['total'] == 1
    assert shares['items'][0]['grantee_id'] == b_id
    assert shares['items'][0]['permission'] == 'viewer'

    # B 现在可见 + 可读
    b_list2 = await design_system_service.list_visible(session, viewer_owner_hasn_id=b_id)
    assert any(it['id'] == ds_id for it in b_list2['items'])
    got = await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=b_id)
    assert got['id'] == ds_id

    # 撤销 → 重回不可见 / 不可读
    res = await design_system_service.revoke_share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, grantee_type='human', grantee_id=b_id
    )
    assert res['revoked'] is True
    b_list3 = await design_system_service.list_visible(session, viewer_owner_hasn_id=b_id)
    assert all(it['id'] != ds_id for it in b_list3['items'])
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=b_id)


async def test_viewer_cannot_save_editor_pending_then_adopt_rollback(session) -> None:
    """viewer 协作分身不能改；editor 改出「待确认版」不动当前态；owner 采用/回滚回填评分。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b_id = f'h_b_{tag}'
    collab = Subject.agent(f'a_collab_{tag}', owner_hasn_id=b_id)

    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'col-{tag}', name='协作底版',
        content=_content('#111111', score=70, grade='fair'), score=70, grade='fair',
    )
    ds_id = saved['id']
    rev1_id = saved['revision']['id']
    assert saved['pending'] is False

    # 只读分享给协作分身 → 不能改
    await design_system_service.share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id,
        grantee_type='agent', grantee_id=collab.hasn_id, permission='viewer',
    )
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.save(
            session, subject=collab, design_system_id=ds_id, slug=f'col-{tag}', name='非法改',
            content=_content('#222222', score=80, grade='good'),
        )

    # 升为 editor → 改出「待确认版」，不动当前版
    await design_system_service.share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id,
        grantee_type='agent', grantee_id=collab.hasn_id, permission='editor',
    )
    pend = await design_system_service.save(
        session, subject=collab, design_system_id=ds_id, slug=f'col-{tag}', name='协作新版',
        content=_content('#33aaff', score=88, grade='good'),
    )
    assert pend['pending'] is True
    rev2_id = pend['revision']['id']

    # owner 视角：当前版仍是 rev1、评分未被协作改动
    got = await design_system_service.get(session, design_system_id=ds_id, viewer_owner_hasn_id=a.hasn_id)
    assert got['current_revision_id'] == rev1_id
    assert got['score'] == 70

    revs = await design_system_service.list_revisions(
        session, design_system_id=ds_id, viewer_owner_hasn_id=a.hasn_id
    )
    assert revs['total'] == 2

    # owner 采用待确认版 → 落当前 + 评分回填自该版报告 summary
    adopted = await design_system_service.set_current_revision(
        session, design_system_id=ds_id, revision_id=rev2_id, owner_hasn_id=a.hasn_id
    )
    assert adopted['current_revision_id'] == rev2_id
    assert adopted['score'] == 88 and adopted['grade'] == 'good'

    # 回滚到 rev1 → 评分回落（确定性，从 rev1 报告 summary 取）
    rolled = await design_system_service.set_current_revision(
        session, design_system_id=ds_id, revision_id=rev1_id, owner_hasn_id=a.hasn_id
    )
    assert rolled['current_revision_id'] == rev1_id
    assert rolled['score'] == 70 and rolled['grade'] == 'fair'


async def test_set_current_revision_owner_only(session) -> None:
    """set_current 是 owner 专属裁决：协作方即便有 editor 也不能裁决落版。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    b_id = f'h_b_{tag}'

    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'oo-{tag}', name='裁决专属',
        content=_content('#121212', score=70, grade='fair'),
    )
    ds_id = saved['id']
    rev1_id = saved['revision']['id']

    await design_system_service.share(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id,
        grantee_type='human', grantee_id=b_id, permission='editor',
    )
    with pytest.raises(errors.ForbiddenError):
        await design_system_service.set_current_revision(
            session, design_system_id=ds_id, revision_id=rev1_id, owner_hasn_id=b_id
        )


async def test_remove_collaborator(session) -> None:
    """owner 解绑协作分身：首删 removed=True，再删 removed=False（幂等）。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    agent_id = f'a_x_{tag}'

    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'rm-{tag}', name='解绑',
        content=_content('#131313', score=70, grade='fair'),
    )
    ds_id = saved['id']
    await design_system_service.add_collaborator(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, agent_hasn_id=agent_id
    )
    r1 = await design_system_service.remove_collaborator(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, agent_hasn_id=agent_id
    )
    assert r1['removed'] is True
    r2 = await design_system_service.remove_collaborator(
        session, design_system_id=ds_id, owner_hasn_id=a.hasn_id, agent_hasn_id=agent_id
    )
    assert r2['removed'] is False


async def test_share_rejects_manager(session) -> None:
    """manager 是 owner 专属档位，不开放授予。"""
    tag = uuid.uuid4().hex[:8]
    a = Subject.human(f'h_a_{tag}')
    saved = await design_system_service.save(
        session, subject=a, design_system_id=None, slug=f'mg-{tag}', name='档位',
        content=_content('#141414', score=70, grade='fair'),
    )
    with pytest.raises(errors.RequestError):
        await design_system_service.share(
            session, design_system_id=saved['id'], owner_hasn_id=a.hasn_id,
            grantee_type='human', grantee_id=f'h_z_{tag}', permission='manager',
        )
