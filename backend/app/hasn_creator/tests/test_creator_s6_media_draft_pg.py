"""创作运营 S6 真实 PG 验收（素材库 media + 草稿箱 draft·§6.7/§6.8）。

覆盖（零 mock，事务末尾回滚不污染库）：
- media.add：type ∈ 白名单校验；asset_uri 必须 hasn://asset/ 引用（禁 base64 铁律）。
- media.list：项目内按 type 过滤 + 倒序。
- media.update：白名单字段（标签/描述/文件名/缩略图）。
- media.delete：删引用行。
- draft.create：title 必填；content/media/tags/target_platforms 入库。
- draft.update：白名单字段。
- draft.list / draft.delete。
- draft.promote：草稿转正为 Content（进创作流水线）+ 删原草稿行。
需要本地 PostgreSQL :15432。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.service.creator_service import creator_service
from backend.app.hasn_creator.service.scope_context import CreatorScope
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_UID = 920601
_HASN = 'hasn:test:creator-s6'


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


def _scope() -> CreatorScope:
    return CreatorScope(user_id=_UID, owner_hasn_id=_HASN)


async def _new_project(session) -> int:
    proj = await creator_service.create_project(
        session, user_id=_UID, scope=_scope(), name='素材草稿测试号', primary_platform='xiaohongshu'
    )
    return proj['id']


async def test_media_add_gate_and_list_filter(session) -> None:
    """media.add：type 白名单 + asset_uri 必须 hasn://asset/；list 按 type 过滤倒序。"""
    scope = _scope()
    pid = await _new_project(session)

    # 目录外类型 → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.add_media(
            session, user_id=_UID, scope=scope, project_id=pid, media_type='pdf', asset_uri='hasn://asset/x'
        )

    # 非 hasn://asset/ 引用（含 base64 形状）→ 拒（铁律）
    with pytest.raises(errors.RequestError):
        await creator_service.add_media(
            session,
            user_id=_UID,
            scope=scope,
            project_id=pid,
            media_type='image',
            asset_uri='data:image/png;base64,iVBORw0KGgo=',
        )

    # 合法图片素材 → 成
    img = await creator_service.add_media(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        media_type='image',
        asset_uri='hasn://asset/img001',
        fields={'filename': '封面.png', 'width': 1080, 'height': 1440, 'tags': {'kind': 'cover'}},
    )
    assert img['type'] == 'image'
    assert img['asset_uri'] == 'hasn://asset/img001'
    assert img['width'] == 1080

    # 视频素材
    await creator_service.add_media(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        media_type='video',
        asset_uri='hasn://asset/vid001',
        fields={'duration': 30},
    )

    # 全量列表 2 条
    all_media = await creator_service.list_media(session, user_id=_UID, scope=scope, project_id=pid)
    assert len(all_media) == 2

    # 按 type=image 过滤 1 条
    only_img = await creator_service.list_media(session, user_id=_UID, scope=scope, project_id=pid, media_type='image')
    assert len(only_img) == 1
    assert only_img[0]['type'] == 'image'

    # 过滤非法 type → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.list_media(session, user_id=_UID, scope=scope, project_id=pid, media_type='pdf')


async def test_media_update_and_delete(session) -> None:
    """media.update 白名单字段（标签/描述/文件名）；delete 删行。"""
    scope = _scope()
    pid = await _new_project(session)
    m = await creator_service.add_media(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        media_type='template',
        asset_uri='hasn://asset/tpl001',
        fields={'filename': '模板v1'},
    )
    mid = m['id']

    updated = await creator_service.update_media(
        session,
        user_id=_UID,
        scope=scope,
        media_id=mid,
        fields={
            'filename': '模板v2',
            'description': '爆款封面模板',
            'tags': {'style': 'minimal'},
            'type': 'image',
        },  # type 不在白名单 → 应被忽略
    )
    assert updated['filename'] == '模板v2'
    assert updated['description'] == '爆款封面模板'
    assert updated['tags'].get('style') == 'minimal'
    assert updated['type'] == 'template'  # 未被越权改动

    # 删除
    res = await creator_service.delete_media(session, user_id=_UID, scope=scope, media_id=mid)
    assert res['deleted'] is True
    rest = await creator_service.list_media(session, user_id=_UID, scope=scope, project_id=pid)
    assert all(x['id'] != mid for x in rest)


async def test_draft_crud_and_promote(session) -> None:
    """draft.create/update/list/delete + promote 转正为 Content 并删原草稿。"""
    scope = _scope()
    pid = await _new_project(session)

    # title 必填 → 空拒
    with pytest.raises(errors.RequestError):
        await creator_service.create_draft(session, user_id=_UID, scope=scope, project_id=pid, title='   ')

    d = await creator_service.create_draft(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        title='灵感：三伏天快手菜',
        fields={
            'content': '开头钩子...',
            'media': ['hasn://asset/img001'],
            'tags': ['夏日'],
            'target_platforms': ['xiaohongshu', 'douyin'],
        },
    )
    did = d['id']
    assert d['title'] == '灵感：三伏天快手菜'
    assert d['media'] == ['hasn://asset/img001']
    assert d['target_platforms'] == ['xiaohongshu', 'douyin']

    # 改草稿
    upd = await creator_service.update_draft(
        session,
        user_id=_UID,
        scope=scope,
        draft_id=did,
        fields={'content': '完整正文...', 'tags': ['夏日', '减脂']},
    )
    assert upd['content'] == '完整正文...'
    assert upd['tags'] == ['夏日', '减脂']

    # 列表 1 条
    drafts = await creator_service.list_drafts(session, user_id=_UID, scope=scope, project_id=pid)
    assert len(drafts) == 1

    # 转正为 Content：沿用 title/target_platforms 建 article 内容，删原草稿
    content = await creator_service.promote_draft(
        session, user_id=_UID, scope=scope, draft_id=did, created_by_agent_id='hasn:agent:tester'
    )
    assert content['title'] == '灵感：三伏天快手菜'
    assert content['content_tracks'] == 'article'
    assert content['status'] == 'idea'
    assert 'xiaohongshu' in (content['target_platforms'] or [])

    # 原草稿已删除
    after = await creator_service.list_drafts(session, user_id=_UID, scope=scope, project_id=pid)
    assert len(after) == 0

    # 转正后再 promote 已不存在的草稿 → NotFound
    with pytest.raises(errors.NotFoundError):
        await creator_service.promote_draft(session, user_id=_UID, scope=scope, draft_id=did)
