"""矢量设计应用（app_id=design，源自 OpenPencil，模块 14 doc27）独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
模块目录与 PG schema 统一用 `hasn_` 前缀（app_id 仍为 `design`，URL 仍为 /api/v1/hasn_design/*）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin: created_time/updated_time）之上注入 `schema='hasn_design'`，
使 codegen 生成的 model 继承它即落到 `hasn_design.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets、通用产物索引 public.hasn_artifacts、
协作共享 public.hasn_resource_share 等）仍留 public，跨 schema 全限定引用。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/
27-OpenPencil矢量设计工具接入设计(本地sidecar·画布即应用).md §5.9。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=design，见上）
APP_SCHEMA = 'hasn_design'


class HasnDesignAppBase(Base):
    """hasn_design 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_design。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
