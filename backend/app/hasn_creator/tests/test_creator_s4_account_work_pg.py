"""创作运营 S4 真实 PG 验收（账号指标透出 + work 作品表 + account.add 条件必填 + 竞品录真收紧）。

覆盖（零 mock，事务末尾回滚不污染库）：
- account.add：platform 校验 ∈ 目录；有公开主页平台 home_url 必填、公众号豁免。
- account.update / update_metrics：手填 + 抓取回填指标 + metrics_json 扩展 + metrics_updated_at 刷新。
- work.upsert/list（own）：归并键 external_id/url 去重、指标刷新、collected_at。
- competitor.log 收紧：platform+url 必填；researched=true 时 follower_count+works_count 必填。
- competitor.update + competitor.works.upsert：调研回填 + works_count 随抓取自动刷新。
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

_UID = 920401
_HASN = 'hasn:test:creator-s4'


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
        session, user_id=_UID, scope=_scope(), name='一人食快手菜号', primary_platform='xiaohongshu'
    )
    return proj['id']


async def test_account_add_platform_and_home_url_gate(session) -> None:
    """platform 校验 ∈ 目录；有公开主页平台 home_url 必填、公众号豁免。"""
    scope = _scope()
    pid = await _new_project(session)

    # 目录外平台 → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.add_account(session, user_id=_UID, scope=scope, project_id=pid, platform='不存在的平台')

    # 小红书（has_public_home）无 home_url → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.add_account(session, user_id=_UID, scope=scope, project_id=pid, platform='xiaohongshu')

    # 小红书带 home_url → 成
    acc = await creator_service.add_account(
        session, user_id=_UID, scope=scope, project_id=pid, platform='xiaohongshu',
        fields={'home_url': 'https://www.xiaohongshu.com/user/profile/abc', 'nickname': '菜菜'},
    )
    assert acc['platform'] == 'xiaohongshu'
    assert acc['home_url'].endswith('/abc')

    # 公众号（has_public_home=false）无 home_url → 豁免，成
    mp = await creator_service.add_account(session, user_id=_UID, scope=scope, project_id=pid, platform='wechat_mp')
    assert mp['platform'] == 'wechat_mp'
    assert mp['home_url'] is None


async def test_account_update_and_metrics(session) -> None:
    """account.update 手填指标 + update_metrics 抓取回填（已知列落列，平台特有并入 metrics_json）。"""
    scope = _scope()
    pid = await _new_project(session)
    acc = await creator_service.add_account(
        session, user_id=_UID, scope=scope, project_id=pid, platform='douyin',
        fields={'home_url': 'https://www.douyin.com/user/x'},
    )
    aid = acc['id']

    # 人手填资料 + 设主账号 + 手填部分指标
    updated = await creator_service.update_account(
        session, user_id=_UID, scope=scope, account_id=aid,
        fields={'nickname': '快手菜阿姨', 'is_primary': True, 'followers': 1200},
    )
    assert updated['nickname'] == '快手菜阿姨'
    assert updated['is_primary'] is True
    assert updated['followers'] == 1200
    assert updated['metrics_updated_at'] is not None

    # 分身抓取回填：已知列 + 平台特有键（入 metrics_json）
    m = await creator_service.update_account_metrics(
        session, user_id=_UID, scope=scope, account_id=aid,
        metrics={'followers': 3400, 'total_likes': 88000, 'total_posts': 42, '获赞率': '3.2%'},
    )
    assert m['followers'] == 3400
    assert m['total_likes'] == 88000
    assert m['total_posts'] == 42
    assert m['metrics_json'].get('获赞率') == '3.2%'


async def test_update_metrics_alias_normalization(session) -> None:
    """分身传的自然/平台特有 key（如小红书「获赞与收藏」合并数、posts_count）归一到规范列；抓取元数据留 metrics_json。"""
    scope = _scope()
    pid = await _new_project(session)
    acc = await creator_service.add_account(
        session, user_id=_UID, scope=scope, project_id=pid, platform='xiaohongshu',
        fields={'home_url': 'https://www.xiaohongshu.com/user/x'},
    )
    aid = acc['id']

    m = await creator_service.update_account_metrics(
        session, user_id=_UID, scope=scope, account_id=aid,
        metrics={
            # 小红书主页「获赞与收藏」是合并数字 → 归一到 total_likes（否则会被静默塞进 metrics_json、页面读列显示 0）
            'xiaohongshu_total_likes_and_favorites': 1352,
            'fans': 106,                 # 别名 → followers
            'posts_count': 40,           # 别名 → total_posts
            'scraped_posts_count': 31,   # 抓取元数据（非规范、无别名）→ 保留 metrics_json
            'scraped_posts_has_more': True,
        },
    )
    # 别名归一到规范列
    assert m['total_likes'] == 1352
    assert m['followers'] == 106
    assert m['total_posts'] == 40
    # 抓取元数据保留原始口径，不丢
    assert m['metrics_json'].get('scraped_posts_count') == 31
    assert m['metrics_json'].get('scraped_posts_has_more') is True
    # 已归一到规范列的键不再残留 metrics_json（避免陈旧值遮挡）
    assert 'xiaohongshu_total_likes_and_favorites' not in m['metrics_json']

    # 纯函数归一自身校验（快、无 DB）
    assert creator_service._normalize_metric_key('likes') == 'total_likes'
    assert creator_service._normalize_metric_key('collections') == 'total_favorites'
    assert creator_service._normalize_metric_key('total_posts') == 'total_posts'
    assert creator_service._normalize_metric_key('scraped_posts_count') is None


async def test_work_upsert_merge_and_list(session) -> None:
    """work.upsert 归并键 external_id/url 去重 + 指标刷新；list 倒序。"""
    scope = _scope()
    pid = await _new_project(session)
    acc = await creator_service.add_account(
        session, user_id=_UID, scope=scope, project_id=pid, platform='bilibili',
        fields={'home_url': 'https://space.bilibili.com/1'},
    )
    aid = acc['id']

    # 首次插入两条
    r1 = await creator_service.upsert_works(
        session, user_id=_UID, scope=scope, source_type='own', owner_ref_id=aid,
        items=[
            {'external_id': 'bv001', 'url': 'https://b23.tv/bv001', 'title': '10分钟快手菜', 'views': 1000, 'likes': 50},
            {'url': 'https://b23.tv/bv002', 'title': '减脂餐', 'views': 500},
        ],
    )
    assert r1['upserted'] == 2

    # 再次 upsert：bv001 按 external_id 归并（更新指标不新增）、新增 bv003
    r2 = await creator_service.upsert_works(
        session, user_id=_UID, scope=scope, source_type='own', owner_ref_id=aid,
        items=[
            {'external_id': 'bv001', 'title': '10分钟快手菜（更新）', 'views': 2000, 'likes': 120},
            {'url': 'https://b23.tv/bv003', 'title': '新视频', 'views': 10},
        ],
    )
    assert r2['upserted'] == 2

    works = await creator_service.list_works(session, user_id=_UID, scope=scope, source_type='own', owner_ref_id=aid)
    # 共 3 条（bv001 归并未重复）
    assert len(works) == 3
    bv001 = next(w for w in works if w['external_id'] == 'bv001')
    assert bv001['views'] == 2000
    assert bv001['likes'] == 120
    assert bv001['title'] == '10分钟快手菜（更新）'
    assert bv001['collected_at'] is not None


async def test_competitor_log_tightened_and_research(session) -> None:
    """competitor.log 收紧：platform+url 必填；researched=true 时 follower_count+works_count 必填。"""
    scope = _scope()
    pid = await _new_project(session)

    # 缺 platform → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.log_competitor(
            session, user_id=_UID, scope=scope, project_id=pid, name='对标号', fields={'url': 'https://x.com/a'}
        )
    # 缺 url → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.log_competitor(
            session, user_id=_UID, scope=scope, project_id=pid, name='对标号', fields={'platform': 'douyin'}
        )
    # researched=true 缺指标 → 拒
    with pytest.raises(errors.RequestError):
        await creator_service.log_competitor(
            session, user_id=_UID, scope=scope, project_id=pid, name='对标号',
            fields={'platform': 'douyin', 'url': 'https://www.douyin.com/user/a', 'researched': True},
        )

    # 先挂 URL 待调研（researched=false，指标待补）→ 成
    comp = await creator_service.log_competitor(
        session, user_id=_UID, scope=scope, project_id=pid, name='对标号',
        fields={'platform': 'douyin', 'url': 'https://www.douyin.com/user/a'},
    )
    assert comp['platform'] == 'douyin'
    assert comp['follower_count'] == 0
    assert comp['works_count'] == 0
    assert comp['last_analyzed'] is None
    cid = comp['id']

    # 分身调研回填
    upd = await creator_service.update_competitor(
        session, user_id=_UID, scope=scope, competitor_id=cid,
        fields={'follower_count': 50000, 'works_count': 210, 'content_style': '口播干货', 'strengths': ['更新快']},
    )
    assert upd['follower_count'] == 50000
    assert upd['works_count'] == 210
    assert upd['last_analyzed'] is not None

    # 竞品作品样本 upsert → works_count 随抓取刷新
    await creator_service.upsert_works(
        session, user_id=_UID, scope=scope, source_type='competitor', owner_ref_id=cid,
        items=[
            {'url': 'https://www.douyin.com/video/1', 'title': '样本1', 'likes': 999},
            {'url': 'https://www.douyin.com/video/2', 'title': '样本2', 'likes': 888},
        ],
    )
    comp_works = await creator_service.list_works(
        session, user_id=_UID, scope=scope, source_type='competitor', owner_ref_id=cid
    )
    assert len(comp_works) == 2
    # 回读竞品，works_count 应刷新为抓取到的作品数
    comps = await creator_service.list_competitors(session, user_id=_UID, scope=scope, project_id=pid)
    assert next(c for c in comps if c['id'] == cid)['works_count'] == 2


async def test_researched_competitor_log_ok(session) -> None:
    """researched=true 带齐 follower_count+works_count → 成，last_analyzed 落时间。"""
    scope = _scope()
    pid = await _new_project(session)
    comp = await creator_service.log_competitor(
        session, user_id=_UID, scope=scope, project_id=pid, name='已调研号',
        fields={
            'platform': 'xiaohongshu', 'url': 'https://www.xiaohongshu.com/user/profile/z',
            'researched': True, 'follower_count': 12000, 'works_count': 88,
        },
    )
    assert comp['follower_count'] == 12000
    assert comp['works_count'] == 88
    assert comp['last_analyzed'] is not None
