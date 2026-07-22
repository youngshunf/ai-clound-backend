"""hasn_project 应用独立 PG schema 基类。

每个 AI-Native 应用使用独立 PG schema（ADR-15：AI-Native 应用命名空间与目录约定）。
本基类在 fba `Base`（created_time/updated_time DateTimeMixin）之上注入 `schema='hasn_project'`，
使本应用 model 继承它即落到 `hasn_project.*` schema，无需逐表手写 `__table_args__`。
共享表（身份 public.hasn_humans/hasn_agents、产物 public.hasn_artifacts、资产 public.hasn_assets）
仍留 public，跨 schema 全限定引用；联邦挂靠只在 public 侧表挂可空 project_id 列（doc38 §4）。

注意（codegen 修正）：fba codegen 默认生成继承裸 `Base` 的 model（落 public），且默认 bigint 主键；
本应用根表 `hasn_project` 的云端权威 ID 必须是 UUID（进 hasn://project/{id} URI），故手工改为
继承本基类 + UUID 主键，对齐 SQL（backend/sql/hasn_project/hasn_project.sql）。

设计事实源：docs/hasn-node设计文档/14-AI-Native应用平台/38-项目管理一级应用(平台项目·联邦挂靠)设计.md §3/§4。
"""

from sqlalchemy.orm import declared_attr

from backend.common.model import Base

# 本应用的 PG schema 名（hasn_ 前缀；app_id=project，URL /api/v1/project/*）
APP_SCHEMA = 'hasn_project'


class HasnProjectAppBase(Base):
    """hasn_project 应用模型基类：created_time/updated_time（继承自 fba Base）+ schema=hasn_project。"""

    __abstract__ = True

    @declared_attr.directive
    def __table_args__(cls) -> dict:  # noqa: N805
        return {'comment': cls.__doc__ or '', 'schema': APP_SCHEMA}
