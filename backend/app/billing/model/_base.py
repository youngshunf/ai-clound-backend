"""billing 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
PG schema 统一用 `hasn_` 前缀（app_id 仍为 `billing`，URL 仍为 /api/v1/pay/* 与
/api/v1/user_tier/*——本模块由 `app/pay` + `app/user_tier` 按 ADR-15 §4 合并而来，
URL 前缀保持不变以兼容 daemon `domains/billing/cloud.rs::BillingCloud` 的代理路径）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_billing'`，
使各 model 继承它即落到 `hasn_billing.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、用户 public.sys_user、new-api 映射等）仍留原 schema，
跨 schema 全限定引用；ORM 经各自基类自动落到所属 schema。

⚠️ 存量表搬迁（ADR-15 §5 §7）：billing 的 13 张表（pay_* 7 张，含 pay_app 支付应用配置
+ credit_*/subscription_*/user_*/model_credit_rate 6 张）已存在于 public，由
`backend/sql/billing/migrations/2026-06-14-move-billing-tables-to-hasn-billing-schema.sql`
幂等 `ALTER TABLE ... SET SCHEMA hasn_billing` 搬迁；ORM 经本基类自动落到新 schema。
⚠️ 凡裸 raw SQL（`text(...)`）引用 billing 表必须显式全限定 `hasn_billing.<table>`
（无 search_path 配置时默认只解析 public，搬迁后裸名/`public.` 前缀都会失效）。
"""

from typing import Any

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=billing，见上）
APP_SCHEMA = 'hasn_billing'


class BillingBase(Base):
    """billing 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_billing。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict[str, Any] | tuple[Any, ...]:  # ruff:ignore[invalid-first-argument-name-for-method]
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
