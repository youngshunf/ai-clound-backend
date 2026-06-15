"""创作运营 owner 业务 API（M8）真实 PG 验收（零 mock，事务末尾回滚）。

owner 面端点直调验证：身份取自 request.user.id、scope 经 resolve_creator_scope（无 HasnHumans
记录→回落 personal）、包裹 creator_service、返回统一信封（ResponseModel code=200 + data）。
覆盖 owner 全链路：建项目→设画像→加账号→建内容→备稿→提交发布→取成品包→批准→标记已发→回填数据→总览。
JWT/中间件级活体 HTTP 测试在 M12 全链路 E2E（打运行中 :8020）覆盖；此处验证 owner 端点业务正确性。
需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn_creator.api.v1.app import creator as owner_api
from backend.app.hasn_creator.schema.owner import (
    AddAccountParam,
    CreateContentParam,
    CreateProjectParam,
    LogInsightParam,
    MarkPublishedParam,
    SaveStageParam,
    SetProfileParam,
    SubmitPublishParam,
    UpdateContentParam,
    UpdateMetricsParam,
)
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_UID = 925101


def _req(uid: int = _UID):
    """构造最小 Request 替身：owner 端点只读 request.user.id（中间件注入的字段）。"""
    return SimpleNamespace(user=SimpleNamespace(id=uid))


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


def _ok(resp):
    """断言统一信封并取出 data。"""
    assert resp.code == 200, f'非成功信封: code={resp.code} msg={getattr(resp, "msg", None)}'
    return resp.data


async def test_owner_full_chain(session) -> None:
    """owner 端点全链路：建项目→画像→账号→内容→备稿→发布→成品包→批准→已发→数据→总览。"""
    req = _req()

    # 建项目（落 personal 归属：无 HasnHumans 记录 → scope 回落 personal）
    proj = _ok(
        await owner_api.create_project(req, session, CreateProjectParam(name='美食号', primary_platform='xiaohongshu'))
    )
    pid = proj['id']
    assert proj['owner_scope'] == 'personal'
    assert proj['user_id'] == _UID

    # 列表（信封带 scope 元信息）
    listed = _ok(await owner_api.list_projects(req, session, status=None, view='team', limit=50))
    assert any(p['id'] == pid for p in listed['items'])
    assert listed['scope']['owner_scope'] == 'personal'

    # 设画像（1:1 upsert）
    prof = _ok(
        await owner_api.set_profile(
            req, session, pid, SetProfileParam(fields={'niche': '家常菜', 'persona': '邻家厨房'})
        )
    )
    assert prof['niche'] == '家常菜'

    # 加平台账号
    acc = _ok(
        await owner_api.add_account(
            req, session, pid, AddAccountParam(platform='xiaohongshu', fields={'nickname': '家常菜小厨'})
        )
    )
    aid = acc['id']

    # 项目详情聚合（含画像 + 账号 + 计数）
    detail = _ok(await owner_api.get_project(req, session, pid))
    assert detail['profile']['niche'] == '家常菜'
    assert len(detail['accounts']) == 1

    # 建内容 + 备稿（终稿 + 封面）
    content = _ok(
        await owner_api.create_content(req, session, CreateContentParam(project_id=pid, title='3步搞定红烧肉'))
    )
    cid = content['id']
    _ok(
        await owner_api.save_stage(
            req, session, cid, SaveStageParam(stage='final_draft', content_text='【红烧肉】第一步…第二步…第三步…')
        )
    )
    _ok(await owner_api.save_stage(req, session, cid, SaveStageParam(stage='cover', asset_refs=['hasn://asset/c1.png'])))
    # 话题标签写进 metadata
    _ok(await owner_api.update_content(req, session, cid, UpdateContentParam(metadata={'hashtags': ['#家常菜']})))

    # 提交发布 → pending_review
    sub = _ok(
        await owner_api.submit_publish(
            req, session, SubmitPublishParam(content_id=cid, account_id=aid, publish_note='晚8点发')
        )
    )
    pub_id = sub['publish_id']
    assert sub['status'] == 'pending_review'

    # 成品包（文案 + 封面 + 话题 + 建议）
    pkg = _ok(await owner_api.get_publish_package(req, session, pub_id))
    assert pkg['ready'] is True
    assert pkg['cover'] == 'hasn://asset/c1.png'
    assert pkg['hashtags'] == ['#家常菜']
    assert pkg['account']['nickname'] == '家常菜小厨'

    # 审核推进内容 → reviewing → ready+approved
    _ok(await owner_api.update_content(req, session, cid, UpdateContentParam(status='reviewing')))
    _ok(
        await owner_api.update_content(
            req, session, cid, UpdateContentParam(status='ready', review_status='approved')
        )
    )

    # 批准发布（人专属）→ 标记已发布回填链接 → 回填数据
    _ok(await owner_api.approve_publish(req, session, pub_id))
    pub = _ok(await owner_api.mark_published(req, session, pub_id, MarkPublishedParam(publish_url='https://xhs/abc')))
    assert pub['status'] == 'published'
    _ok(await owner_api.update_metrics(req, session, pub_id, UpdateMetricsParam(metrics={'views': 9000, 'likes': 600})))

    # 已发布后成品包反映最终态
    pkg2 = _ok(await owner_api.get_publish_package(req, session, pub_id))
    assert pkg2['status'] == 'published'
    assert pkg2['publish_url'] == 'https://xhs/abc'

    # 运营总览（信封）
    overview = _ok(await owner_api.report_overview(req, session, project_id=None, view='team'))
    assert isinstance(overview, dict)


async def test_owner_insight_writeback(session) -> None:
    """owner 复盘端点：log_insight 原子回写 pillar_weights（进化核心；信封 + data 校验）。

    pillar_weights 是进化核心，非 owner 直接可设字段（set_profile 白名单不含）——仅由 insight 回写。
    空基线 + delta 0.5 → 0.5；再叠加 0.5 → 1.0，验证累加而非覆盖。
    """
    req = _req(uid=925102)
    proj = _ok(await owner_api.create_project(req, session, CreateProjectParam(name='知识号')))
    pid = proj['id']

    res = _ok(
        await owner_api.log_insight(
            req,
            session,
            LogInsightParam(
                project_id=pid,
                insight_type='pillar',
                summary='教程类表现更好，加权',
                proposed_action={'pillar_weight_delta': {'教程': 0.5}},
            ),
        )
    )
    assert isinstance(res, dict)
    prof = _ok(await owner_api.get_profile(req, session, pid))
    assert prof['pillar_weights']['教程'] == pytest.approx(0.5)

    # 再次复盘叠加（累加语义，非覆盖）
    _ok(
        await owner_api.log_insight(
            req,
            session,
            LogInsightParam(
                project_id=pid,
                insight_type='pillar',
                summary='继续加权',
                proposed_action={'pillar_weight_delta': {'教程': 0.5}},
            ),
        )
    )
    prof2 = _ok(await owner_api.get_profile(req, session, pid))
    assert prof2['pillar_weights']['教程'] == pytest.approx(1.0)


async def test_owner_cross_user_isolation(session) -> None:
    """他人不可读本人项目（owner scope 隔离，回 NotFoundError）。"""
    from backend.common.exception import errors

    proj = _ok(await owner_api.create_project(_req(uid=925201), session, CreateProjectParam(name='A号')))
    with pytest.raises(errors.NotFoundError):
        await owner_api.get_project(_req(uid=925202), session, proj['id'])
