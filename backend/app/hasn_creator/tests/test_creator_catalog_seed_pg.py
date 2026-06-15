"""创作运营上架 M7 真实 PG 验收（catalog/manifest/entitlement 注册 + 企业自播种，零 mock，回滚）。

覆盖：catalog 自播种含 creator（免费/已上架/sort_order）、builtin manifest 可发布、
企业开通自播种 playbook 幂等。需要本地 PostgreSQL :15432（DATABASE_PORT）。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.hasn.service import app_catalog_service as catalog_svc
from backend.app.hasn.service.ai_native_app_registry import ai_native_app_registry
from backend.app.hasn_creator.model.playbook import Playbook
from backend.app.hasn_creator.service.enterprise_seed_service import ensure_creator_enterprise_seeded
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio

_ENT = 924099


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


async def test_catalog_seeds_creator(session) -> None:
    """catalog 自播种后 creator 上架（免费/published/sort_order=50/manual 不默认挂载）。"""
    await catalog_svc.ensure_catalog_seeded(session)
    cat = await catalog_svc.get_catalog(session, app_id='creator')
    assert cat is not None
    assert cat.status == 'published'
    assert cat.access_type == 'free'
    assert cat.sort_order == 50
    assert cat.default_mount is False  # install_policy=manual
    assert cat.execution_mode == 'cloud'
    assert 'personal' in cat.scope and 'enterprise' in cat.scope


async def test_creator_manifest_publishable(session) -> None:
    """builtin manifest 校验通过且可发布（21 工具）。"""
    payload = await ai_native_app_registry.ensure_builtin_published(session, 'creator')
    assert payload['app_id'] == 'creator'
    assert len(payload['manifest_json']['tools']) == 21


async def test_enterprise_seed_idempotent(session) -> None:
    """企业开通自播种 playbook：首次复制内置模板，重复开通不重复播种。"""
    # 种一个内置 playbook（M9 hub 之前手动模拟）
    session.add(
        Playbook(
            user_id=None,
            enterprise_id=None,
            name='美食号标准打法',
            goal='稳定涨粉',
            content_strategy={'pillars': ['教程', '探店']},
            cadence={'frequency': 'daily'},
            tone_guide='温暖',
            red_lines=['不蹭热点低俗'],
            is_builtin=True,
        )
    )
    await session.flush()

    added = await ensure_creator_enterprise_seeded(session, enterprise_id=_ENT)
    # M9 seed 后库内已有真实内置 playbook（种草号/科普号/品牌号）→ 复制「本测试手动加的 1 条 + 真实内置」；
    # 只断言 ≥1 且本测试那条被复制（精确数受全局 seed 影响，不锁死）。
    assert added >= 1
    # 企业副本：enterprise_id=本企业、is_builtin=False、content_strategy 拷贝
    copy = (
        (
            await session.execute(
                select(Playbook).where(Playbook.enterprise_id == _ENT, Playbook.name == '美食号标准打法')
            )
        )
        .scalars()
        .first()
    )
    assert copy is not None
    assert copy.is_builtin is False
    assert copy.content_strategy == {'pillars': ['教程', '探店']}
    assert copy.red_lines == ['不蹭热点低俗']

    # 重复开通 → 幂等不重复
    again = await ensure_creator_enterprise_seeded(session, enterprise_id=_ENT)
    assert again == 0


async def test_enterprise_seed_idempotent_on_fresh_enterprise(session) -> None:
    """全新企业重复开通幂等：第二次必为 0（不论 DB 内置模板多少，第一次复制后第二次不再复制）。"""
    first = await ensure_creator_enterprise_seeded(session, enterprise_id=924088)
    assert first >= 0  # 无模板→0；有模板→N（M9 seed 后）
    second = await ensure_creator_enterprise_seeded(session, enterprise_id=924088)
    assert second == 0


async def test_enterprise_seed_zero_when_no_enterprise(session) -> None:
    """enterprise_id 为 0/空 → 直接返回 0（个人开通不播种）。"""
    assert await ensure_creator_enterprise_seeded(session, enterprise_id=0) == 0
