"""knowledge 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录 + hasn_ 前缀铁律）。
本基类在 fba `Base`（bigint `id_key` + DateTimeMixin）之上注入 `schema='hasn_knowledge'`，
使 codegen 生成的 model 继承它即落到 `hasn_knowledge.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、资产 public.hasn_assets）仍留 public，跨 schema 全限定引用。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（注意：catalog app_id='knowledge' 不带前缀，schema/模块带 hasn_ 前缀）
APP_SCHEMA = 'hasn_knowledge'


class KnowledgeBase(Base):
    """knowledge 应用模型基类：bigint 自增主键 + created_time/updated_time + schema=hasn_knowledge。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
