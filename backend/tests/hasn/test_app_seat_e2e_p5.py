"""P5 企业席位购买·核心 E2E（真实 PostgreSQL，零 mock，DATABASE_PORT=15432）。

覆盖实施清单 §P5「核心 E2E」7 场景，端到端跑真实 service 路径（不打桩）：

  1. 企业购买某双模应用 5 席 → entitlement seats_total=5；
  2. owner/admin 指派张三/李四/王五 → seats_used=3；三人企业空间该应用 ``entitled``；
  3. 第 4 成员（未指派）企业空间该应用 ``need_seat_assignment``；
  4. 张三退出企业 → 其席位 released，seats_used=2，其企业空间该应用回落 ``need_seat_assignment``；
  5. 席位复用：新成员赵六可被指派进释放出的空位；
  6. 隔离：个人购买 vs 企业购买互不串（企业买席位不改 owner 维度准入）；
  7. 纯企业应用（purchasable_by=enterprise）个人购买入口 → 4xx（RequestError）。

真实链路：settle_seat_purchase（购买结算）→ workbench_domain_service.assign_app_seat（指派，
经 M3 hasn_id 翻译 + S4 FOR UPDATE）→ resolve_app_access(subject_type='enterprise')（S1 席位闸）→
remove_member（P4 生命周期钩子释放席位）→ check_purchasable_by（P1-4 购买入口守卫）。

事实源: docs/hasn-node设计文档/12-企业与组织/04-应用与空间关系及企业席位购买设计.md §5-§6。
"""

from __future__ import annotations

import uuid

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import pytest_asyncio
import sqlalchemy as sa

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog
from backend.app.hasn.model.hasn_enterprise import HasnEnterprise
from backend.app.hasn.model.hasn_enterprise_membership import HasnEnterpriseMembership
from backend.app.hasn.model.hasn_humans import HasnHumans
from backend.app.hasn.service import app_catalog_service, app_seat_service
from backend.app.hasn.service.workbench_domain_service import workbench_domain_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL

pytestmark = pytest.mark.asyncio


def _uid() -> str:
    return uuid.uuid4().hex[:8]


@pytest_asyncio.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(sa.select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')

    session = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        yield session
    finally:
        await session.rollback()
        await session.close()
        await engine.dispose()


async def _seed_catalog(
    db: AsyncSession, *, purchasable_by: str = 'both', scope: list[str] | None = None
) -> HasnAppCatalog:
    """造一条 published + purchase 计价的目录行（默认双模双买）。app_id 随机避污染。"""
    cat = HasnAppCatalog(
        app_id=f'seat_e2e_{_uid()}',
        name=f'席位E2E应用 {_uid()}',
        status='published',
        access_type='purchase',
        scope=scope if scope is not None else ['personal', 'enterprise'],
        purchasable_by=purchasable_by,
        price_amount=Decimal(99),
        price_unit='cny',
        billing_cycle='month',
    )
    db.add(cat)
    await db.flush()
    return cat


async def _seed_human(db: AsyncSession, *, user_id: int, nickname: str) -> str:
    hasn_id = f'h_{_uid()}{_uid()}'[:38]
    db.add(HasnHumans(hasn_id=hasn_id, star_id=f's{user_id}', user_id=user_id, nickname=nickname))
    await db.flush()
    return hasn_id


async def _seed_enterprise(db: AsyncSession, *, owner_user_id: int) -> int:
    ent = HasnEnterprise(name=f'席位E2E企业 {_uid()}', slug=f'seate2e-{_uid()}', owner_user_id=owner_user_id)
    db.add(ent)
    await db.flush()
    return ent.id


async def _seed_member(db: AsyncSession, *, enterprise_id: int, nickname: str, role: str = 'member') -> tuple[int, str]:
    """造企业成员（HasnHumans + approved membership），返回 (user_id, owner hasn_id)。"""
    user_id = 950_000_000 + int(_uid(), 16) % 1_000_000
    hasn_id = await _seed_human(db, user_id=user_id, nickname=nickname)
    db.add(
        HasnEnterpriseMembership(
            enterprise_id=enterprise_id,
            user_id=user_id,
            role=role,
            status='approved',
        )
    )
    await db.flush()
    return user_id, hasn_id


async def _enterprise_access(
    db: AsyncSession, *, catalog: HasnAppCatalog, enterprise_id: int, member_hasn_id: str
) -> dict:
    """以企业维度（subject_type='enterprise'）判定某成员对该 app 的准入。"""
    return await app_catalog_service.resolve_app_access(
        db,
        catalog=catalog,
        owner_hasn_id=member_hasn_id,
        subject_type='enterprise',
        subject_id=str(enterprise_id),
        member_hasn_id=member_hasn_id,
    )


# ============================ 核心 E2E 主流程（场景 1-5） ============================


async def test_seat_purchase_assign_lifecycle_e2e(db: AsyncSession) -> None:
    """场景 1-5：购买 5 席 → 指派 3 人 entitled → 第 4 人 need_seat_assignment →
    张三退出席位释放回落 → 赵六复用空位。"""
    owner_uid = 951_000_000 + int(_uid(), 16) % 1_000_000
    await _seed_human(db, user_id=owner_uid, nickname='老板')  # resolve_owner_hasn_id(assigned_by)
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    catalog = await _seed_catalog(db)
    app_id = catalog.app_id

    zhangsan_uid, zhangsan = await _seed_member(db, enterprise_id=ent_id, nickname='张三')
    _lisi_uid, lisi = await _seed_member(db, enterprise_id=ent_id, nickname='李四')
    _wangwu_uid, wangwu = await _seed_member(db, enterprise_id=ent_id, nickname='王五')
    _forth_uid, forth = await _seed_member(db, enterprise_id=ent_id, nickname='第四人')

    # —— 场景 1：企业购买 5 席 → seats_total=5 ——
    ent = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=5, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    assert ent.seats_total == 5

    # 购买后、指派前：已购成员企业空间仍 need_seat_assignment（S1 席位闸）
    pre = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=zhangsan)
    assert pre['allowed'] is False
    assert pre['reason'] == 'need_seat_assignment'
    assert pre['requires'] == 'seat'

    # —— 场景 2：owner/admin 指派张三/李四/王五 → seats_used=3，三人 entitled ——
    for member in (zhangsan, lisi, wangwu):
        seat = await workbench_domain_service.assign_app_seat(
            db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member, operator_user_id=owner_uid
        )
        assert seat['status'] == 'assigned'
    assert await app_seat_service.count_seats_used(db, entitlement_id=ent.id) == 3

    for member in (zhangsan, lisi, wangwu):
        acc = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=member)
        assert acc['allowed'] is True, member
        assert acc['reason'] == 'entitled'

    # —— 场景 3：第 4 成员（未指派）→ need_seat_assignment ——
    acc4 = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=forth)
    assert acc4['allowed'] is False
    assert acc4['reason'] == 'need_seat_assignment'

    # —— 场景 4：张三退出企业 → 席位 released，seats_used=2，回落 need_seat_assignment ——
    await workbench_domain_service.remove_member(db, enterprise_id=ent_id, user_id=zhangsan_uid)
    assert await app_seat_service.count_seats_used(db, entitlement_id=ent.id) == 2
    acc_gone = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=zhangsan)
    assert acc_gone['allowed'] is False
    assert acc_gone['reason'] == 'need_seat_assignment'
    # 李四/王五不受影响
    for member in (lisi, wangwu):
        acc = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=member)
        assert acc['allowed'] is True, member

    # —— 场景 5：席位复用——赵六可被指派进释放出的空位 ——
    _zhaoliu_uid, zhaoliu = await _seed_member(db, enterprise_id=ent_id, nickname='赵六')
    seat = await workbench_domain_service.assign_app_seat(
        db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=zhaoliu, operator_user_id=owner_uid
    )
    assert seat['status'] == 'assigned'
    assert await app_seat_service.count_seats_used(db, entitlement_id=ent.id) == 3  # 2 + 赵六
    acc_zhao = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=zhaoliu)
    assert acc_zhao['allowed'] is True
    assert acc_zhao['reason'] == 'entitled'


# ============================ 场景 6：个人 vs 企业购买隔离 ============================


async def test_personal_and_enterprise_purchase_isolated(db: AsyncSession) -> None:
    """企业买席位只影响企业维度；同一成员的 owner 维度准入不受影响（仍 need_purchase）。
    反向：owner 个人买断后，owner 维度 entitled，但企业维度不因个人购买而放行。"""
    owner_uid = 952_000_000 + int(_uid(), 16) % 1_000_000
    await _seed_human(db, user_id=owner_uid, nickname='老板6')
    ent_id = await _seed_enterprise(db, owner_user_id=owner_uid)
    catalog = await _seed_catalog(db)
    app_id = catalog.app_id
    _muid, member = await _seed_member(db, enterprise_id=ent_id, nickname='成员6')

    # 企业买 3 席并指派该成员 → 企业维度 entitled
    ent = await app_seat_service.settle_seat_purchase(
        db, enterprise_id=ent_id, app_id=app_id, seats=3, billing_cycle='month', order_ref=f'o-{_uid()}'
    )
    await workbench_domain_service.assign_app_seat(
        db, enterprise_id=ent_id, app_id=app_id, member_hasn_id=member, operator_user_id=owner_uid
    )
    ent_acc = await _enterprise_access(db, catalog=catalog, enterprise_id=ent_id, member_hasn_id=member)
    assert ent_acc['allowed'] is True and ent_acc['reason'] == 'entitled'

    # 同一成员的 owner 维度（个人空间）：没个人权益 → 企业席位不串过来 → need_purchase
    owner_acc = await app_catalog_service.resolve_app_access(
        db, catalog=catalog, owner_hasn_id=member, subject_type='owner'
    )
    assert owner_acc['allowed'] is False
    assert owner_acc['reason'] == 'need_purchase'

    # 反向：owner 个人买断 → owner 维度 entitled，但不影响企业维度语义（企业仍走席位闸）
    await app_catalog_service.grant_entitlement(
        db,
        app_id=app_id,
        subject_type='owner',
        subject_id=member,
        source='purchase',
        order_ref=f'o-{_uid()}',
        expires_at=app_catalog_service.purchase_expiry('month'),
    )
    owner_acc2 = await app_catalog_service.resolve_app_access(
        db, catalog=catalog, owner_hasn_id=member, subject_type='owner'
    )
    assert owner_acc2['allowed'] is True and owner_acc2['reason'] == 'entitled'
    # 企业维度仍由席位决定（该成员有席 → entitled；与个人权益无关，seats_total 未被个人购买改动）
    await db.refresh(ent)
    assert ent.seats_total == 3


# ============================ 场景 7：纯企业应用个人购买入口 4xx ============================


async def test_enterprise_only_app_personal_purchase_rejected(db: AsyncSession) -> None:
    """purchasable_by=enterprise 的纯企业应用，个人（buyer='owner'）购买/试用入口 → RequestError。"""
    catalog = await _seed_catalog(db, purchasable_by='enterprise')
    with pytest.raises(errors.RequestError):
        app_catalog_service.check_purchasable_by(catalog, buyer='owner')
    # 企业买家放行（不抛）
    app_catalog_service.check_purchasable_by(catalog, buyer='enterprise')
