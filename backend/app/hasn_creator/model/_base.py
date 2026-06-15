"""hasn_creator 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin: created_time/updated_time）之上注入
`schema='hasn_creator'`，使本应用 model 继承它即落到 `hasn_creator.*` schema，无需逐表手写
`__table_args__`。共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets、
企业 public.hasn_enterprise* 等）仍留 public，跨 schema 全限定引用。

设计事实源：docs/自媒体创作运营/00-自媒体创作运营全链路AI-Native应用设计.md §5；施工 91 §2。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（= app_id 对应的 schema，ADR-15）
APP_SCHEMA = 'hasn_creator'


class HasnCreatorAppBase(Base):
    """hasn_creator 应用模型基类：bigint 自增主键 + created_time/updated_time（继承自 fba Base）+ schema=hasn_creator。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
