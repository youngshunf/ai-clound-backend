"""付费墙特征键集中注册表（feature_key = 付费墙通用语言）。

feature_key 是付费墙判定的稳定标识，贯穿三处：
- billing_offering.feature_key（商品目录声明它卖的是哪种能力）
- hasn_app_entitlement.feature_key（权益记录它解锁了哪种能力）
- resolve_access(feature_key=...)（判定入口按它查权益）

集中在此注册 + 启动期校验，杜绝各处硬编码 feature_key 字符串导致的漂移。
设计事实源：docs/hasn-node设计文档/16-订阅与积分计费/02-统一商业化内核设计.md §8.1（K0 拍板）。

两类形态：
- **固定键**：全局唯一的特征（如 llm:tier、webapp:hosting）。
- **前缀族**：一类特征按实例展开（如 app:<app_id>、seat:<app_id>），前缀已注册即视为合法。
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.billing.model.billing_plan import BillingPlan

TIER_OFFERING_KEY = 'llm:tier'
TIER_FEATURE_KEYS: frozenset[str] = frozenset({'每月积分', '云存储', '分身数量', '客服支持'})
ACTIVE_TIER_PLAN_KEYS: frozenset[str] = frozenset({
    'free',
    'lite',
    'lite_yearly',
    'pro',
    'pro_yearly',
    'max',
    'max_yearly',
    'ultra',
    'ultra_yearly',
})

# 固定 feature_key：全局唯一，无实例后缀
FIXED_FEATURE_KEYS: frozenset[str] = frozenset({
    'llm:tier',  # LLM 订阅档（订阅制唯一特征；档位差异走 plan_key）
    'credits:topup',  # 积分充值（消耗型 top-up，非门控；不同包为不同 plan_key）
    'webapp:hosting',  # 网页应用全栈托管（doc06；MK-1 预定义键，暂不 seed offering 行）
    # 云端常驻节点：为主人在云端托管一个**无头 hasn-node 实例**（每订阅一容器 = 主人的第 N 台设备）。
    # 与上面的 `webapp:hosting` 是两码事，别混：
    #   - `webapp:hosting`  → 托管「主人的网页应用」（doc06 全栈托管，卖的是 Web 应用运行环境）
    #   - `cloud_node`      → 托管「主人的分身节点」（无头 hasn-node 容器，卖的是常驻在线的设备位）
    # 混用会让付费墙拿错商品、按错价，故此处显式注释区分。
    'cloud_node',
})

# 前缀族：<prefix><instance_id>，instance_id 必须非空
PREFIX_FEATURE_KEYS: frozenset[str] = frozenset({
    'app:',  # AI-Native 应用权益（app:<app_id>，如 app:quant）
    'seat:',  # 企业席位（seat:<app_id>，席位制应用的席位特征）
    'workflow_template:',  # 工作流场景模板付费挂钩（workflow_template:<template_key>，doc94 §10-P7）
})


def is_registered(feature_key: str) -> bool:
    """判断 feature_key 是否为注册表已知的合法特征键。"""
    if not feature_key:
        return False
    if feature_key in FIXED_FEATURE_KEYS:
        return True
    return any(feature_key.startswith(prefix) and len(feature_key) > len(prefix) for prefix in PREFIX_FEATURE_KEYS)


def validate_feature_keys(feature_keys: list[str]) -> list[str]:
    """批量校验一组 feature_key，返回其中未注册的（违规）列表。"""
    return [fk for fk in feature_keys if not is_registered(fk)]


def _storage_feature_label(storage_bytes: object) -> str | None:
    """把二进制存储配额格式化为定价卡片文案。"""
    if isinstance(storage_bytes, bool) or not isinstance(storage_bytes, int) or storage_bytes <= 0:
        return None
    tib = 1024**4
    gib = 1024**3
    if storage_bytes % tib == 0:
        return f'{storage_bytes // tib} TB'
    if storage_bytes % gib == 0:
        return f'{storage_bytes // gib} GB'
    return None


def validate_tier_plan_payload(*, plan_key: str, quota_json: dict | None, display_json: dict | None) -> list[str]:
    """校验单条订阅 plan 的卡片权益与实际配额一致。"""
    quota = quota_json if isinstance(quota_json, dict) else {}
    display = display_json if isinstance(display_json, dict) else {}
    features = display.get('features')
    prefix = f'plan={plan_key!r}'
    if not isinstance(features, dict):
        return [f'{prefix} display_json.features 缺失或不是对象']

    violations: list[str] = []
    feature_keys = set(features)
    if feature_keys != TIER_FEATURE_KEYS:
        missing = sorted(TIER_FEATURE_KEYS - feature_keys)
        extra = sorted(feature_keys - TIER_FEATURE_KEYS)
        violations.append(f'{prefix} features 键不合法: missing={missing} extra={extra}')

    try:
        quota_credits = Decimal(str(quota.get('credits_per_cycle')))
        feature_credits = Decimal(str(features.get('每月积分')))
    except (InvalidOperation, TypeError, ValueError):
        violations.append(f'{prefix} 每月积分不是可比较数值')
    else:
        if feature_credits != quota_credits:
            violations.append(f'{prefix} 每月积分漂移: features={feature_credits} quota={quota_credits}')

    expected_storage = _storage_feature_label(quota.get('storage_bytes'))
    if expected_storage is None:
        violations.append(f'{prefix} storage_bytes 必须是正整数 GiB/TiB')
    elif features.get('云存储') != expected_storage:
        violations.append(f'{prefix} 云存储漂移: features={features.get("云存储")!r} quota={expected_storage!r}')

    max_agents = quota.get('max_agents')
    if isinstance(max_agents, bool) or not isinstance(max_agents, int) or max_agents == 0 or max_agents < -1:
        violations.append(f'{prefix} max_agents 必须是正整数或 -1')
    else:
        expected_agents: int | str = '不限' if max_agents == -1 else max_agents
        if features.get('分身数量') != expected_agents:
            violations.append(f'{prefix} 分身数量漂移: features={features.get("分身数量")!r} quota={expected_agents!r}')
    return violations


async def validate_tier_catalog_consistency(db: AsyncSession) -> list[str]:
    """校验上架订阅目录的稳定档位集合、周期与权益卡片。"""
    plans = (
        (
            await db.execute(
                select(BillingPlan).where(
                    BillingPlan.offering_key == TIER_OFFERING_KEY,
                    BillingPlan.status == 'active',
                )
            )
        )
        .scalars()
        .all()
    )
    violations: list[str] = []
    actual_keys = {plan.plan_key for plan in plans}
    if actual_keys != ACTIVE_TIER_PLAN_KEYS:
        violations.append(
            '上架档位集合漂移: '
            f'missing={sorted(ACTIVE_TIER_PLAN_KEYS - actual_keys)} '
            f'extra={sorted(actual_keys - ACTIVE_TIER_PLAN_KEYS)}'
        )

    for plan in plans:
        base_key = plan.plan_key.removesuffix('_yearly')
        expected_cycle = 'year' if plan.plan_key.endswith('_yearly') else 'month'
        expected_count = 12 if expected_cycle == 'year' else (None if plan.plan_key == 'free' else 1)
        quota = plan.quota_json if isinstance(plan.quota_json, dict) else {}
        if quota.get('tier') != base_key:
            violations.append(f'plan={plan.plan_key!r} quota_json.tier 应为 {base_key!r}')
        if plan.cycle != expected_cycle:
            violations.append(f'plan={plan.plan_key!r} cycle 应为 {expected_cycle!r}')
        if quota.get('cycle_seconds') != 2_592_000:
            violations.append(f'plan={plan.plan_key!r} cycle_seconds 应为 2592000')
        if quota.get('cycle_count') != expected_count:
            violations.append(f'plan={plan.plan_key!r} cycle_count 应为 {expected_count!r}')
        if quota.get('wallet_overflow') is not True:
            violations.append(f'plan={plan.plan_key!r} wallet_overflow 应为 true')
        violations.extend(
            validate_tier_plan_payload(
                plan_key=plan.plan_key,
                quota_json=plan.quota_json,
                display_json=plan.display_json,
            )
        )
    return violations


async def validate_offering_consistency(db: AsyncSession) -> list[str]:
    """启动期一致性校验：扫描 billing_offering 全表，返回 feature_key 未注册的违规描述列表。

    返回空列表 = 全部合法。调用方（启动钩子 / pytest）据此决定告警或断言。
    不在此处 raise，避免一条脏数据 brick 掉整个后端启动；由调用方按场景决定严格度。
    """
    from backend.app.billing.model.billing_offering import BillingOffering

    rows = (await db.execute(select(BillingOffering.key, BillingOffering.feature_key))).all()
    violations: list[str] = []
    for offering_key, feature_key in rows:
        if not is_registered(feature_key):
            violations.append(f'offering={offering_key!r} feature_key={feature_key!r} 未在 feature_registry 注册')
    return violations


async def validate_catalog_sku_refs(db: AsyncSession) -> list[str]:
    """一致性守卫：扫描 hasn_app_catalog.sku_ref，返回「指向不存在 billing_offering 的悬挂引用」列表（MK-9）。

    ``sku_ref`` 是应用目录挂到商业化商品（offering.key）的对接指针（预留列，可为空）。凡填了值就必须
    命中一条真实 offering，否则前端付费墙据它取价会取空 → 静默付费墙断裂。此守卫进 CI，改价/退役 offering
    时若漏改 catalog 指针即刻报红（对齐 ``validate_offering_consistency`` 的「零漂移」思路）。

    返回空列表 = 全部合法（含全表 sku_ref 皆空的常态）。不在此处 raise，由调用方（启动钩子 / pytest）定严格度。
    """
    from backend.app.billing.model.billing_offering import BillingOffering
    from backend.app.hasn.model.hasn_app_catalog import HasnAppCatalog

    # 全库已注册的 offering.key 集合（一次取全，避免逐行 N 次查库）。
    offering_keys = {k for (k,) in (await db.execute(select(BillingOffering.key))).all() if k}
    rows = (await db.execute(select(HasnAppCatalog.app_id, HasnAppCatalog.sku_ref))).all()
    violations: list[str] = []
    for app_id, sku_ref in rows:
        # 空 / 未填 sku_ref 是合法常态（预留列），跳过；仅校验填了值的行。
        if not sku_ref:
            continue
        if sku_ref not in offering_keys:
            violations.append(f'catalog app_id={app_id!r} sku_ref={sku_ref!r} 悬挂——无对应 billing_offering')
    return violations


async def validate_workflow_template_sku_refs(db: AsyncSession) -> list[str]:
    """一致性守卫：扫描 workflow_template.sku_ref，返回「指向不存在 billing_offering 的悬挂引用」列表（MK-9b）。

    工作流场景模板的 ``sku_ref`` 是「官方付费模板挂到商业化商品（offering.key）」的对接指针（预留列，
    可为空）——语义与 ``hasn_app_catalog.sku_ref`` 完全对齐（指向 offering.key，非 feature_key）。凡填了
    值就必须命中一条真实 offering，否则 P7 付费墙据它取价 / 判权会取空 → 静默付费墙断裂。此守卫进 CI，
    改价 / 退役 offering 时若漏改模板指针即刻报红（镜像 ``validate_catalog_sku_refs``）。

    返回空列表 = 全部合法（含全表 sku_ref 皆空的常态）。不在此处 raise，由调用方（启动钩子 / pytest）定严格度。
    """
    from backend.app.billing.model.billing_offering import BillingOffering
    from backend.app.hasn_task.model.workflow_template import HasnWorkflowTemplate

    # 全库已注册的 offering.key 集合（一次取全，避免逐行 N 次查库）。
    offering_keys = {k for (k,) in (await db.execute(select(BillingOffering.key))).all() if k}
    rows = (await db.execute(select(HasnWorkflowTemplate.template_key, HasnWorkflowTemplate.sku_ref))).all()
    violations: list[str] = []
    for template_key, sku_ref in rows:
        # 空 / 未填 sku_ref 是合法常态（免费模板），跳过；仅校验填了值的付费模板行。
        if not sku_ref:
            continue
        if sku_ref not in offering_keys:
            violations.append(
                f'workflow_template template_key={template_key!r} sku_ref={sku_ref!r} 悬挂——无对应 billing_offering'
            )
    return violations
