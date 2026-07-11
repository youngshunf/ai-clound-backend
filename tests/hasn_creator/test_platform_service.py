"""
S1 平台目录（Platform Registry）服务层真实 PG 测试（零 mock）。

覆盖 CreatorService 的三个平台方法：
- list_platforms：返回内置 seed（≥10 条，按 sort 升序）。
- validate_platform：目录内放行、目录外抛 RequestError、空值放行。
- platform_requires_home_url：has_public_home=true → 需要主页 URL；公众号/视频号 → 豁免。

依赖本地库已跑迁移 `2026-07-10-add-platform-registry.sql`（含 10 条 seed）。
"""

import pytest

from backend.app.hasn_creator.service.creator_service import CreatorService
from backend.common.exception import errors


@pytest.mark.asyncio
async def test_list_platforms_returns_seed_sorted(db_session):
    """内置 seed 全部读出，且按 sort 升序（小红书排最前）。"""
    items = await CreatorService.list_platforms(db_session)
    assert len(items) >= 10
    keys = [it['key'] for it in items]
    # 关键平台都在目录里
    for expected in ('xiaohongshu', 'douyin', 'wechat_mp', 'wechat_channels', 'bilibili'):
        assert expected in keys, f'平台 {expected} 缺失'
    # 按 sort 升序（小红书 sort=10 排第一）
    sorts = [it['sort'] for it in items]
    assert sorts == sorted(sorts), 'platforms 未按 sort 升序返回'
    assert items[0]['key'] == 'xiaohongshu'
    # 指标口径随行返回
    xhs = next(it for it in items if it['key'] == 'xiaohongshu')
    assert xhs['metrics_labels']['followers'] == '粉丝'
    assert xhs['home_url'] == 'https://www.xiaohongshu.com'


@pytest.mark.asyncio
async def test_validate_platform_in_directory_passes(db_session):
    """目录内平台放行，不抛异常。"""
    await CreatorService.validate_platform(db_session, platform='douyin')


@pytest.mark.asyncio
async def test_validate_platform_out_of_directory_raises(db_session):
    """目录外平台抛 RequestError（选择制：不许自由文本乱填）。"""
    with pytest.raises(errors.RequestError):
        await CreatorService.validate_platform(db_session, platform='not_a_real_platform_xyz')


@pytest.mark.asyncio
async def test_validate_platform_empty_passes(db_session):
    """空平台放行（可选字段，不强制）。"""
    await CreatorService.validate_platform(db_session, platform=None)
    await CreatorService.validate_platform(db_session, platform='')


@pytest.mark.asyncio
async def test_platform_requires_home_url(db_session):
    """有公开主页的平台需要 home_url；公众号/视频号豁免；未知平台不强制。"""
    # 小红书有公开主页 → 需要
    assert await CreatorService.platform_requires_home_url(db_session, platform='xiaohongshu') is True
    # 公众号/视频号无公开 web 主页 → 豁免
    assert await CreatorService.platform_requires_home_url(db_session, platform='wechat_mp') is False
    assert await CreatorService.platform_requires_home_url(db_session, platform='wechat_channels') is False
    # 未知/空 → 不强制（校验由 validate_platform 兜）
    assert await CreatorService.platform_requires_home_url(db_session, platform='unknown_xyz') is False
    assert await CreatorService.platform_requires_home_url(db_session, platform=None) is False
