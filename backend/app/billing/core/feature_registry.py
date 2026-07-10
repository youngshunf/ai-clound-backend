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
FIXED_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        'llm:tier',  # LLM 订阅档（订阅制唯一特征；档位差异走 plan_key）
        'credits:topup',  # 积分充值（消耗型 top-up，非门控；不同包为不同 plan_key）
        'webapp:hosting',  # 网页应用全栈托管（doc06；MK-1 预定义键，暂不 seed offering 行）
    }
)

# 前缀族：<prefix><instance_id>，instance_id 必须非空
PREFIX_FEATURE_KEYS: frozenset[str] = frozenset(
    {
        'app:',  # AI-Native 应用权益（app:<app_id>，如 app:quant）
        'seat:',  # 企业席位（seat:<app_id>，席位制应用的席位特征）
    }
)


def is_registered(feature_key: str) -> bool:
    """判断 feature_key 是否为注册表已知的合法特征键。"""
    if not feature_key:
        return False
    if feature_key in FIXED_FEATURE_KEYS:
        return True
    for prefix in PREFIX_FEATURE_KEYS:
        # 前缀命中且实例段非空（app: 后必须跟 app_id）
        if feature_key.startswith(prefix) and len(feature_key) > len(prefix):
            return True
    return False


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
