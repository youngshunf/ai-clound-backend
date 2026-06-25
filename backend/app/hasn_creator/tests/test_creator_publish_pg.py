"""创作运营发布 P0（manual_assist）M6 真实 PG 验收（设计 §10，零 mock，事务末尾回滚）。

manual_assist 主路径：分身备「成品包」（文案+封面/配图+话题标签+发布建议）→ 主人审核 →
复制手动发 → 回填 url/数据。覆盖成品包组装齐备/不齐备、发布状态机闭环、跨户隔离。
需要本地 PostgreSQL :15432（DATABASE_PORT）。
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

_UID = 923001
_HASN = 'hasn:owner:pub-a'


def _scope(uid: int = _UID, hasn: str = _HASN) -> CreatorScope:
    return CreatorScope(user_id=uid, owner_hasn_id=hasn)


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


async def _setup_content_with_account(session, scope):
    proj = await creator_service.create_project(session, user_id=_UID, scope=scope, name='美食号')
    pid = proj['id']
    acc = await creator_service.add_account(
        session,
        user_id=_UID,
        scope=scope,
        project_id=pid,
        platform='xiaohongshu',
        fields={'nickname': '家常菜小厨', 'home_url': 'https://xhs/u/1'},
    )
    content = await creator_service.create_content(
        session, user_id=_UID, scope=scope, project_id=pid, title='3步搞定红烧肉'
    )
    return pid, acc['id'], content['id']


async def test_manual_assist_package_full(session) -> None:
    """成品包齐备：文案+封面+配图+话题标签+发布建议，含发布状态机闭环。"""
    scope = _scope()
    _pid, aid, cid = await _setup_content_with_account(session, scope)

    # 备稿：终稿 + 封面 + 配图
    await creator_service.save_stage(
        session, user_id=_UID, scope=scope, content_id=cid, stage='outline', content_text='钩子→步骤→收尾'
    )
    await creator_service.save_stage(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        stage='final_draft',
        content_text='【3步搞定红烧肉】第一步…第二步…第三步…',
    )
    await creator_service.save_stage(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        stage='cover',
        asset_refs=['hasn://asset/cover-1.png'],
    )
    await creator_service.save_stage(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        stage='storyboard',
        asset_refs=['hasn://asset/img-2.png', 'hasn://asset/img-3.png'],
    )
    # 话题标签写进 content metadata
    await creator_service.update_content(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        metadata={'hashtags': ['#家常菜', '#红烧肉教程']},
    )

    # 提交发布 → pending_review
    sub = await creator_service.submit_publish(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        account_id=aid,
        publish_note='晚8点发，置顶评论问"想看哪道菜"',
    )
    pub_id = sub['publish_id']

    # 成品包（待审阶段即可预览）
    pkg = await creator_service.assemble_publish_package(session, user_id=_UID, scope=scope, publish_id=pub_id)
    assert pkg['ready'] is True
    assert pkg['body_stage'] == 'final_draft'
    assert '红烧肉' in pkg['body_text']
    assert pkg['cover'] == 'hasn://asset/cover-1.png'
    # 配图汇总（封面 + 分镜两张，保序去重）
    assert pkg['assets'] == ['hasn://asset/cover-1.png', 'hasn://asset/img-2.png', 'hasn://asset/img-3.png']
    assert pkg['hashtags'] == ['#家常菜', '#红烧肉教程']
    assert pkg['publish_note'].startswith('晚8点')
    assert pkg['account']['nickname'] == '家常菜小厨'
    assert pkg['platform'] == 'xiaohongshu'
    assert pkg['status'] == 'pending_review'
    assert pkg['publish_url'] is None

    # 审核通过内容 → 批准发布 → 标记已发布（回填 url）→ 回填数据
    await creator_service.update_content(session, user_id=_UID, scope=scope, content_id=cid, status='reviewing')
    await creator_service.update_content(
        session,
        user_id=_UID,
        scope=scope,
        content_id=cid,
        status='ready',
        review_status='approved',
        reviewer_user_id=_UID,
    )
    await creator_service.approve_publish(session, user_id=_UID, scope=scope, publish_id=pub_id, approval_user_id=_UID)
    pub = await creator_service.mark_published(
        session, user_id=_UID, scope=scope, publish_id=pub_id, publish_url='https://xhs/abc'
    )
    assert pub['status'] == 'published'
    await creator_service.update_metrics(
        session,
        user_id=_UID,
        scope=scope,
        publish_id=pub_id,
        metrics={'views': 9000, 'likes': 600, 'new_followers': 30},
    )

    # 已发布后成品包反映最终态 + 回填链接
    pkg2 = await creator_service.assemble_publish_package(session, user_id=_UID, scope=scope, publish_id=pub_id)
    assert pkg2['status'] == 'published'
    assert pkg2['publish_url'] == 'https://xhs/abc'


async def test_package_not_ready_without_draft(session) -> None:
    """无终稿/初稿/大纲 → 成品包 ready=False，body_text 为空（不造假）。"""
    scope = _scope()
    _pid, aid, cid = await _setup_content_with_account(session, scope)
    sub = await creator_service.submit_publish(session, user_id=_UID, scope=scope, content_id=cid, account_id=aid)
    pkg = await creator_service.assemble_publish_package(
        session, user_id=_UID, scope=scope, publish_id=sub['publish_id']
    )
    assert pkg['ready'] is False
    assert pkg['body_text'] is None
    assert pkg['cover'] is None
    assert pkg['assets'] == []
    assert pkg['hashtags'] == []


async def test_package_falls_back_to_outline(session) -> None:
    """无终稿但有大纲 → 正文回落大纲（ready=True，body_stage=outline）。"""
    scope = _scope()
    _pid, aid, cid = await _setup_content_with_account(session, scope)
    await creator_service.save_stage(
        session, user_id=_UID, scope=scope, content_id=cid, stage='outline', content_text='只有大纲'
    )
    sub = await creator_service.submit_publish(session, user_id=_UID, scope=scope, content_id=cid, account_id=aid)
    pkg = await creator_service.assemble_publish_package(
        session, user_id=_UID, scope=scope, publish_id=sub['publish_id']
    )
    assert pkg['ready'] is True
    assert pkg['body_stage'] == 'outline'
    assert pkg['body_text'] == '只有大纲'


async def test_package_cross_user_isolation(session) -> None:
    """他人不可取本人发布的成品包。"""
    scope_a = _scope()
    _pid, aid, cid = await _setup_content_with_account(session, scope_a)
    sub = await creator_service.submit_publish(session, user_id=_UID, scope=scope_a, content_id=cid, account_id=aid)
    scope_b = _scope(uid=923002, hasn='hasn:owner:pub-b')
    with pytest.raises(errors.NotFoundError):
        await creator_service.assemble_publish_package(
            session, user_id=923002, scope=scope_b, publish_id=sub['publish_id']
        )
