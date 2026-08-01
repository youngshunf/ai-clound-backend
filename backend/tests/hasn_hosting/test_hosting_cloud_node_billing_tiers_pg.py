"""云端节点计费档位：附赠 + 加购的混合结构（主人 2026-08-01 拍板）——零 mock，打真实 PostgreSQL。

结构：`llm:tier` 五档 free/lite/pro/max/ultra 中**只有 pro 及以上各附赠 1 个**；超出部分走
`cloud:node` 独立商品**按需加购**（¥128/月 · ¥1228/年）；free 与 lite 都是 0 个且不给试用，
**未定档的档位一律 fail-closed 为 0**（不回落配置默认值静默送资源）。
准入取并集、配额求和：

    允许创建 ⟺ （档位附赠 > 0） 或 （持有有效 cloud_node 权益）
    配额上限  = 档位附赠数 ＋ 权益快照里的加购数

本文件钉死五条**回归守卫**（每一条都对应一个真会出事的场景）：

1. pro 用户零加购也能建 1 个——`cloud_node` 一进 `FIXED_FEATURE_KEYS`，`resolve_access` 就从
   「未知特征放行」翻成「无权益即拒」，附赠会被自家付费墙挡死。这条在服务层合并口径前必红。
2. free 用户被明确拒绝，且错误码是 `cloud_node_subscription_required`（WebUI 空态按它分支）。
3. 配额 = 附赠 + 加购（pro 附赠 1 + 加购 2 = 3）。
4. **`is_entitled` 对「pro 附赠、零加购」必须为 true**——sweep 用它判「订阅到期」，
   判错就是把人家在跑的节点停机、30 天后连数据卷一起销毁。本模块最贵的一种误判。
5. 迁移幂等：连跑两次，行数与 JSON 取值完全一致。

测试自身幂等应用 `2026-08-01-cloud-node-billing-tiers.sql`，可在未迁移的库上跑。
需本地 PostgreSQL :15432。
"""

from __future__ import annotations

import pathlib

from datetime import timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import pytest
import pytest_asyncio

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from backend.app.billing.core import feature_registry
from backend.app.billing.model.billing_offering import BillingOffering
from backend.app.billing.model.billing_plan import BillingPlan
from backend.app.billing.model.user_subscription import UserSubscription
from backend.app.hasn.model.hasn_app_entitlement import HasnAppEntitlement
from backend.app.hasn_hosting.constants import (
    CLOUD_NODE_FEATURE_KEY,
    CLOUD_NODE_OFFERING_KEY,
    ERR_QUOTA_EXCEEDED,
    ERR_SUBSCRIPTION_REQUIRED,
)
from backend.app.hasn_hosting.model import HasnCloudNodes
from backend.app.hasn_hosting.service.cloud_node_service import cloud_node_service
from backend.common.exception import errors
from backend.database.db import SQLALCHEMY_DATABASE_URL, async_engine
from backend.utils.timezone import timezone

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.asyncio

# 每个档位一个独立主体，避免用例间互相污染配额计数
OWNER_PRO = 'h_hosting_tier_pro'
OWNER_FREE = 'h_hosting_tier_free'
OWNER_ADDON = 'h_hosting_tier_addon'
USER_PRO = 990401
USER_FREE = 990402
USER_ADDON = 990403
ALL_USERS = (USER_PRO, USER_FREE, USER_ADDON)
ALL_OWNERS = (OWNER_PRO, OWNER_FREE, OWNER_ADDON)
NODE_PREFIX = 'n_cloud_test_tier_'

#: 零附赠档（主人 2026-08-01 五档定档：free/lite/pro/max/ultra，只有 pro 及以上附赠 1 个）
ZERO_GRANT_TIERS = ('free', 'lite')

# backend/ 根（tests/hasn_hosting/ 的上两级）
_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[2]
    / 'sql' / 'billing' / 'migrations' / '2026-08-01-cloud-node-billing-tiers.sql'
)


def _split_sql(raw: str) -> list[str]:
    """把 .sql 切成单条语句：按 ';' 分割，但**跳过 `$$ … $$` 美元引用块内部**的分号。

    迁移里的 `DO $$ … $$` 复核块整块是一条语句，天真地按分号切会把它劈碎。
    """
    body = '\n'.join(ln for ln in raw.splitlines() if not ln.lstrip().startswith('--'))
    stmts: list[str] = []
    buf: list[str] = []
    idx = 0
    in_dollar = False
    while idx < len(body):
        if body.startswith('$$', idx):
            in_dollar = not in_dollar
            buf.append('$$')
            idx += 2
            continue
        char = body[idx]
        if char == ';' and not in_dollar:
            stmt = ''.join(buf).strip()
            if stmt:
                stmts.append(stmt)
            buf = []
            idx += 1
            continue
        buf.append(char)
        idx += 1
    tail = ''.join(buf).strip()
    if tail:
        stmts.append(tail)
    return stmts


async def _apply_migration(engine) -> None:
    """幂等应用本任务的计费迁移。

    用 `exec_driver_sql` 绕开 SQLAlchemy 把 `'llm:tier'` 里的冒号误当 bindparam 解析。
    """
    async with engine.begin() as conn:
        for stmt in _split_sql(_MIGRATION.read_text(encoding='utf-8')):
            await conn.exec_driver_sql(stmt)


async def _purge(sess) -> None:
    await sess.execute(text('DELETE FROM hasn_cloud_node_events WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await sess.execute(text('DELETE FROM hasn_cloud_nodes WHERE node_id LIKE :p'), {'p': f'{NODE_PREFIX}%'})
    await sess.execute(
        text('DELETE FROM hasn_billing.user_subscription WHERE user_id = ANY(:u)'), {'u': list(ALL_USERS)}
    )
    await sess.execute(
        text('DELETE FROM hasn_app_entitlement WHERE subject_id = ANY(:o)'), {'o': list(ALL_OWNERS)}
    )
    await sess.commit()


def _subscription(
    *, user_id: int, tier: str, plan_snapshot: dict[str, Any] | None = None
) -> UserSubscription:
    """一份有效订阅。

    刻意**不填** `offering_key`/`plan_key`——线上 57 条存量合同这两列全是 NULL
    （free 档由 `credit_grant_service` 建，压根不写），这才是真实形状。
    """
    now = timezone.now()
    return UserSubscription(
        app_code='huanxing',
        user_id=user_id,
        tier=tier,
        subscription_type='monthly',
        monthly_credits=Decimal(0),
        current_credits=Decimal(0),
        used_credits=Decimal(0),
        purchased_credits=Decimal(0),
        billing_cycle_start=now,
        billing_cycle_end=now + timedelta(days=30),
        subscription_start_date=now,
        subscription_end_date=now + timedelta(days=30),
        next_grant_date=None,
        status='active',
        auto_renew=True,
        plan_snapshot=plan_snapshot,
    )


def _addon_entitlement(*, owner_hasn_id: str, nodes: int) -> HasnAppEntitlement:
    """一份 `cloud:node` 加购权益，快照里记着买了几个节点。

    `app_id` 用 feature_key 占位，与 `access_service.grant_trial` 的通用特征写法一致
    （通用特征没有 catalog app_id，用 feature_key 保 `uq_app_entitlement_active` 唯一）。
    """
    return HasnAppEntitlement(
        app_id=CLOUD_NODE_FEATURE_KEY,
        feature_key=CLOUD_NODE_FEATURE_KEY,
        subject_type='owner',
        subject_id=owner_hasn_id,
        source='purchase',
        status='active',
        quota_json={'max_cloud_nodes': nodes},
        granted_at=timezone.now(),
        expires_at=timezone.now() + timedelta(days=30),
    )


def _cloud_node(node_id: str, *, user_id: int, owner_hasn_id: str) -> HasnCloudNodes:
    return HasnCloudNodes(
        node_id=node_id,
        user_id=user_id,
        owner_hasn_id=owner_hasn_id,
        host='hosting-test',
        container_ref=None,
        status='online',
        failure_reason=None,
        failure_detail=None,
        image_version='0.0.1',
        image_digest='sha256:' + '2' * 64,
        credential_session_uuid=None,
        retain_until=None,
        last_backup_at=None,
        online_since=None,
    )


@pytest_asyncio.fixture
async def ctx() -> AsyncIterator:
    await async_engine.dispose()
    engine = create_async_engine(SQLALCHEMY_DATABASE_URL, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.execute(select(1))
    except Exception as exc:
        await engine.dispose()
        pytest.skip(f'本地 PostgreSQL 不可达，跳过: {exc!r}')
    await _apply_migration(engine)  # 幂等，裸库可跑
    sess = async_sessionmaker(engine, expire_on_commit=False)()
    try:
        await _purge(sess)
        yield sess, engine
    finally:
        await _purge(sess)
        await sess.rollback()
        await sess.close()
        await engine.dispose()
        await async_engine.dispose()


# ─── 商品目录：offering / plan 落地形状 ───


async def test_feature_key_registered_and_distinct_from_webapp_hosting(ctx) -> None:
    """`cloud_node` 已注册，且与 `webapp:hosting` 是两个互不相干的特征键。"""
    sess, _ = ctx
    assert feature_registry.is_registered(CLOUD_NODE_FEATURE_KEY)
    assert feature_registry.is_registered('webapp:hosting')
    assert CLOUD_NODE_FEATURE_KEY != 'webapp:hosting'
    # 注册后全库 offering 一致性校验必须仍为空（新 offering 的 feature_key 已合规）
    assert await feature_registry.validate_offering_consistency(sess) == []


async def test_cloud_node_offering_and_plans_seeded(ctx) -> None:
    """`cloud:node` 商品与两个档位按主人拍板的定价落库。"""
    sess, _ = ctx
    off = (
        await sess.execute(select(BillingOffering).where(BillingOffering.key == CLOUD_NODE_OFFERING_KEY))
    ).scalar_one()
    assert off.kind == 'feature_plan'
    assert off.feature_key == CLOUD_NODE_FEATURE_KEY
    assert off.display_name == '云端常驻节点'
    assert off.status == 'active'
    assert off.source == 'platform'

    plans = {
        p.plan_key: p
        for p in (
            await sess.execute(select(BillingPlan).where(BillingPlan.offering_key == CLOUD_NODE_OFFERING_KEY))
        ).scalars().all()
    }
    assert set(plans) == {'monthly', 'yearly'}
    assert plans['monthly'].price_amount == Decimal('128.00')
    assert plans['monthly'].cycle == 'month'
    assert plans['yearly'].price_amount == Decimal('1228.00')
    assert plans['yearly'].cycle == 'year'
    for plan in plans.values():
        assert plan.price_unit == 'cny'
        assert plan.status == 'active'
        assert plan.quota_json['max_cloud_nodes'] == 1
        # 主人拍板：不给试用
        assert plan.trial_json == {'enabled': False}
        # grace_json 对齐 llm:tier 现有档位的实测值（库里全是空对象）
        assert plan.grace_json == {}
        assert plan.display_json['display_name'] == '云端常驻节点'


async def test_llm_tier_plans_carry_max_cloud_nodes(ctx) -> None:
    """`llm:tier` 各档都带上附赠数：五档中 free/lite=0，pro 及以上=1。

    `lite`（¥49）是五档定档新增的低价档，主人明确**只有 pro 及以上才附赠**云端节点；
    写成「除 free 外全是 1」会让 lite 白得一个常驻容器。
    """
    sess, _ = ctx
    plans = (
        await sess.execute(select(BillingPlan).where(BillingPlan.offering_key == 'llm:tier'))
    ).scalars().all()
    if not plans:
        pytest.skip('本地库无 llm:tier 存量档，跳过')
    for plan in plans:
        assert 'max_cloud_nodes' in plan.quota_json, f'{plan.plan_key} 缺 max_cloud_nodes'
        tier = plan.quota_json.get('tier') or plan.plan_key
        expected = 0 if tier in ZERO_GRANT_TIERS else 1
        assert plan.quota_json['max_cloud_nodes'] == expected, f'{plan.plan_key} 附赠数应为 {expected}'


# ─── 守卫 1b：未定档的档位必须 fail-closed 为 0（五档定档的直接后果） ───


async def test_undefined_tier_grants_zero_and_is_rejected(ctx) -> None:
    """档位没定档 `max_cloud_nodes` → 附赠 **0** 且创建被拒（`cloud_node_subscription_required`）。

    改判前这里回落配置 `HOSTING_MAX_NODES_PER_OWNER`（=1）：订阅线新建 `lite`（¥49）时若没写
    `max_cloud_nodes`，托管侧就一路掉到默认 1，**免费级别的档白得一个常驻容器**（真金白银的算力）。
    送资源必须是显式定档的结果，没定档就是 0。用一个库里绝不存在的 tier 名钉死这条兜底。
    """
    sess, _ = ctx
    sub = _subscription(user_id=USER_FREE, tier='someday_unpriced')  # 商品目录里查无此档
    sess.add(sub)
    await sess.commit()

    assert await cloud_node_service._tier_grant(sess, sub) == 0, '未定档的档位必须按 0 计，不得回落配置默认值'
    assert await cloud_node_service._quota_of(sess, sub, owner_hasn_id=OWNER_FREE) == 0
    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE)
    assert exc.value.data == {'error': ERR_SUBSCRIPTION_REQUIRED}
    # sweep 侧口径同样收紧：未定档不算「仍有资格」
    assert await cloud_node_service.is_entitled(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE) is False


async def test_defined_pro_tier_still_grants_one(ctx) -> None:
    """兜底改判**不得误伤正常路径**：`pro` 显式定档仍是 1（快照 / 目录反查两条路都验）。"""
    sess, _ = ctx
    # 路径①：合同快照已固化（迁移 C 回填后的形状）
    snap_sub = _subscription(user_id=USER_PRO, tier='pro', plan_snapshot={'tier': 'pro', 'max_cloud_nodes': 1})
    sess.add(snap_sub)
    await sess.commit()
    assert await cloud_node_service._tier_grant(sess, snap_sub) == 1

    # 路径②：存量形状（快照为空、offering_key 为 NULL）→ 按 tier 名反查商品目录
    catalog_sub = _subscription(user_id=USER_ADDON, tier='pro')
    sess.add(catalog_sub)
    await sess.commit()
    assert await cloud_node_service._tier_grant(sess, catalog_sub) == 1
    await cloud_node_service._assert_can_create(sess, user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON)


# ─── 守卫 1：pro 零加购也能建（服务层合并口径前必红） ───


async def test_pro_tier_grant_allows_one_node_without_any_addon(ctx) -> None:
    """pro 用户零加购必须能建 1 个。

    这是注册 `cloud_node` feature_key 后的**回归守卫**：注册前 `resolve_access` 走「未知特征放行」，
    注册后变成「无权益即拒」——若准入闸不改成「附赠 ∪ 加购」，这里会直接 403，
    等于用付费墙把自己卖出去的附赠挡在门外。
    """
    sess, _ = ctx
    sub = _subscription(user_id=USER_PRO, tier='pro')  # plan_snapshot=None，走 tier 名反查
    sess.add(sub)
    await sess.commit()

    assert await cloud_node_service._tier_grant(sess, sub) == 1
    # 零加购：既无 cloud_node 权益行，也不该被算作持有
    assert await cloud_node_service._addon_access(sess, owner_hasn_id=OWNER_PRO) == (False, 0)
    assert await cloud_node_service._quota_of(sess, sub, owner_hasn_id=OWNER_PRO) == 1

    # 不抛即通过
    await cloud_node_service._assert_can_create(sess, user_id=USER_PRO, owner_hasn_id=OWNER_PRO)

    # 附赠只有 1 个：占满后第二个必须落配额闸（而不是订阅闸）
    sess.add(_cloud_node(f'{NODE_PREFIX}pro1', user_id=USER_PRO, owner_hasn_id=OWNER_PRO))
    await sess.commit()
    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_PRO, owner_hasn_id=OWNER_PRO)
    assert exc.value.data is not None
    assert exc.value.data['error'] == ERR_QUOTA_EXCEEDED
    assert exc.value.data['quota'] == 1


# ─── 守卫 2：free 明确拒绝，且错误码不变 ───


async def test_free_tier_is_rejected_with_subscription_required(ctx) -> None:
    """free 档 0 个、不给试用：必须明确拒绝，错误码保持 `cloud_node_subscription_required`。"""
    sess, _ = ctx
    # 存量形状：plan_snapshot 为空、offering_key 为 NULL——只能靠 tier 名反查判成 0，
    # 少了那一步会掉到配置默认值 1，免费档白拿一个云端节点。
    sub = _subscription(user_id=USER_FREE, tier='free')
    sess.add(sub)
    await sess.commit()

    assert await cloud_node_service._tier_grant(sess, sub) == 0
    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE)
    assert exc.value.data == {'error': ERR_SUBSCRIPTION_REQUIRED}

    # 迁移回填后的形状（plan_snapshot 已固化 0）同样拒绝
    sub.plan_snapshot = {'tier': 'free', 'max_cloud_nodes': 0}
    await sess.commit()
    assert await cloud_node_service._tier_grant(sess, sub) == 0
    with pytest.raises(errors.ForbiddenError) as exc2:
        await cloud_node_service._assert_can_create(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE)
    assert exc2.value.data == {'error': ERR_SUBSCRIPTION_REQUIRED}

    # free 档不给试用 → 商品目录里 trial 必须是关的，否则前端会渲染出试用入口
    plan = (
        await sess.execute(
            select(BillingPlan).where(
                BillingPlan.offering_key == CLOUD_NODE_OFFERING_KEY, BillingPlan.plan_key == 'monthly'
            )
        )
    ).scalar_one()
    assert plan.trial_json.get('enabled') is False


# ─── 守卫 3：配额 = 附赠 + 加购 ───


async def test_quota_is_tier_grant_plus_addon(ctx) -> None:
    """pro 附赠 1 + 加购 2 = 3：求和，不是取大也不是二选一。"""
    sess, _ = ctx
    sub = _subscription(user_id=USER_ADDON, tier='pro')
    sess.add(sub)
    sess.add(_addon_entitlement(owner_hasn_id=OWNER_ADDON, nodes=2))
    await sess.commit()

    assert await cloud_node_service._tier_grant(sess, sub) == 1
    assert await cloud_node_service._addon_access(sess, owner_hasn_id=OWNER_ADDON) == (True, 2)
    assert await cloud_node_service._quota_of(sess, sub, owner_hasn_id=OWNER_ADDON) == 3

    for seq in range(3):
        await cloud_node_service._assert_can_create(sess, user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON)
        sess.add(_cloud_node(f'{NODE_PREFIX}addon{seq}', user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON))
        await sess.commit()

    with pytest.raises(errors.ForbiddenError) as exc:
        await cloud_node_service._assert_can_create(sess, user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON)
    assert exc.value.data is not None
    assert exc.value.data['error'] == ERR_QUOTA_EXCEEDED
    assert exc.value.data['used'] == 3
    assert exc.value.data['quota'] == 3


async def test_addon_alone_entitles_without_paid_tier(ctx) -> None:
    """只买加购、没有付费档（free 用户加购）也应放行——并集的另一半。"""
    sess, _ = ctx
    sess.add(_subscription(user_id=USER_ADDON, tier='free', plan_snapshot={'max_cloud_nodes': 0}))
    sess.add(_addon_entitlement(owner_hasn_id=OWNER_ADDON, nodes=1))
    await sess.commit()

    await cloud_node_service._assert_can_create(sess, user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON)
    assert await cloud_node_service.is_entitled(sess, user_id=USER_ADDON, owner_hasn_id=OWNER_ADDON) is True


# ─── 守卫 4：sweep 判存活必须认附赠（最贵的一条） ───


async def test_is_entitled_true_for_bundled_pro_without_addon(ctx) -> None:
    """pro 附赠、零加购的用户，sweep 必须判 true。

    判 false 的后果：`run_cloud_node_retention_sweep` 会把他正在跑的节点停机、进 30 天保留期，
    逾期连数据卷一起销毁。这条红了就是在删用户数据。
    """
    sess, _ = ctx
    sess.add(_subscription(user_id=USER_PRO, tier='pro'))
    await sess.commit()

    assert await cloud_node_service.is_entitled(sess, user_id=USER_PRO, owner_hasn_id=OWNER_PRO) is True


async def test_is_entitled_false_for_free_and_for_no_subscription(ctx) -> None:
    """free 档与无订阅都判 false——否则免费用户的节点永远不会被回收。"""
    sess, _ = ctx
    # 无任何订阅
    assert await cloud_node_service.is_entitled(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE) is False

    sess.add(_subscription(user_id=USER_FREE, tier='free'))
    await sess.commit()
    assert await cloud_node_service.is_entitled(sess, user_id=USER_FREE, owner_hasn_id=OWNER_FREE) is False


# ─── 守卫 5：迁移幂等 ───


async def test_migration_is_idempotent(ctx) -> None:
    """连跑两次结果一致：行数不漂移、JSON 取值不变。"""
    sess, engine = ctx

    async def _snapshot() -> tuple[int, int, list[tuple[str, Any]], list[tuple[int, Any]]]:
        off = (
            await sess.execute(
                select(func.count()).select_from(BillingOffering).where(
                    BillingOffering.key == CLOUD_NODE_OFFERING_KEY
                )
            )
        ).scalar_one()
        plan_rows = (
            await sess.execute(
                select(func.count()).select_from(BillingPlan).where(
                    BillingPlan.offering_key == CLOUD_NODE_OFFERING_KEY
                )
            )
        ).scalar_one()
        tiers = [
            (r[0], r[1])
            for r in (
                await sess.execute(
                    select(BillingPlan.plan_key, BillingPlan.quota_json['max_cloud_nodes'].astext)
                    .where(BillingPlan.offering_key == 'llm:tier')
                    .order_by(BillingPlan.plan_key)
                )
            ).all()
        ]
        subs = [
            (r[0], r[1])
            for r in (
                await sess.execute(
                    select(UserSubscription.id, UserSubscription.plan_snapshot['max_cloud_nodes'].astext)
                    .order_by(UserSubscription.id)
                )
            ).all()
        ]
        return off, plan_rows, tiers, subs

    before = await _snapshot()
    assert before[0] == 1 and before[1] == 2

    await _apply_migration(engine)  # 幂等重跑
    await sess.commit()  # 丢掉本 session 的读快照，重新取数

    after = await _snapshot()
    assert after == before, f'迁移重跑后结果漂移:\nbefore={before}\nafter={after}'
