"""marketplace 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
PG schema 统一用 `hasn_` 前缀（app_id 仍为 `marketplace`，URL 仍为 /api/v1/marketplace/* 等）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_marketplace'`，
使各 model 继承它即落到 `hasn_marketplace.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets）仍留 public，跨 schema 全限定引用。

⚠️ 存量表搬迁（ADR-15 §5 §7）：marketplace 表已存在于 public，由
`backend/sql/marketplace/migrations/2026-06-14-move-marketplace-tables-to-hasn-marketplace-schema.sql`
幂等 `ALTER TABLE ... SET SCHEMA hasn_marketplace` 搬迁；ORM 经本基类自动落到新 schema。
⚠️ 凡裸 raw SQL（`text(...)`）引用 marketplace 表必须显式全限定 `hasn_marketplace.<table>`
（无 search_path 配置时默认只解析 public，搬迁后裸名/`public.` 前缀都会失效）。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=marketplace，见上）
APP_SCHEMA = 'hasn_marketplace'


class MarketplaceBase(Base):
    """marketplace 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_marketplace。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
