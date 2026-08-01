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

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

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
